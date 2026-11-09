package org.lessonlistener.app;

import android.Manifest;
import android.app.Activity;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.media.MediaRecorder;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.OpenableColumns;
import android.text.Editable;
import android.text.InputType;
import android.text.TextWatcher;
import android.util.Base64;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.Spinner;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.SocketTimeoutException;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;

public final class MainActivity extends Activity {
    private static final int REQ_MIC = 201;
    private static final int REQ_IMAGE = 202;
    private static final int REQ_EXPORT = 203;
    private static final int MAX_AUDIO_SECONDS = 45;
    private static final int MAX_IMAGE_BYTES = 12 * 1024 * 1024;
    private static final int MAX_HTTP_BYTES = 2 * 1024 * 1024;

    private static final int BG = Color.rgb(16, 19, 22);
    private static final int SURFACE = Color.rgb(28, 33, 38);
    private static final int SURFACE_ALT = Color.rgb(36, 43, 49);
    private static final int TEXT = Color.rgb(239, 243, 245);
    private static final int MUTED = Color.rgb(164, 177, 185);
    private static final int ACCENT = Color.rgb(121, 224, 194);
    private static final int DANGER = Color.rgb(255, 139, 131);

    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final ExecutorService networkExecutor = Executors.newSingleThreadExecutor();
    private final Map<String, JSONObject> profiles = new LinkedHashMap<>();
    private SharedPreferences prefs;
    private SharedPreferences contexts;
    private SharedPreferences settings;
    private JSONObject schedule;

    private LinearLayout root;
    private FrameLayout contentFrame;
    private TextView globalStatus;
    private TextView lessonTabButton;
    private TextView notesTabButton;
    private TextView settingsTabButton;
    private TextView answerView;
    private TextView lessonStatusView;
    private EditText questionField;
    private EditText contextField;
    private EditText keyField;
    private EditText modelField;
    private Spinner providerSpinner;
    private ProgressBar progressBar;

    private String currentTab = "lesson";
    private String activeSubject = "algebra";
    private String activeProvider = "gemini";
    private String questionDraft = "";
    private String answer = "";
    private String answerQuestion = "";
    private String imageName = "";
    private Uri selectedImage;
    private Uri exportTarget;
    private String statusText = "Готово к уроку";
    private boolean busy;
    private boolean destroyed;
    private boolean recording;
    private boolean showSchedule;
    private long recordingStartedAt;
    private File audioFile;
    private MediaRecorder recorder;
    private Future<?> activeTask;
    private volatile HttpURLConnection activeConnection;
    private Runnable recordingTicker;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().setStatusBarColor(BG);
        getWindow().setNavigationBarColor(BG);
        prefs = getSharedPreferences("private_settings", MODE_PRIVATE);
        contexts = getSharedPreferences("subject_contexts", MODE_PRIVATE);
        settings = getSharedPreferences("provider_settings", MODE_PRIVATE);
        activeSubject = prefs.getString("active_subject", "algebra");
        activeProvider = prefs.getString("active_provider", "gemini");
        loadBundledData();
        if (!profiles.containsKey(activeSubject) && !profiles.isEmpty()) {
            activeSubject = profiles.keySet().iterator().next();
        }
        answer = prefs.getString("last_answer", "");
        answerQuestion = prefs.getString("last_question", "");
        buildShell();
        renderCurrentTab();
    }

    private void loadBundledData() {
        try {
            for (String asset : getAssets().list("profiles")) {
                if (!asset.endsWith(".json")) continue;
                JSONObject profile = new JSONObject(readAsset("profiles/" + asset));
                profiles.put(profile.optString("id", asset.replace(".json", "")), profile);
            }
            schedule = new JSONObject(readAsset("schedule.json"));
        } catch (Exception ignored) {
            schedule = new JSONObject();
            statusText = "Не удалось загрузить профили. Проверьте комплектность APK.";
        }
    }

    private String readAsset(String path) throws IOException {
        try (InputStream in = getAssets().open(path); ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[8192];
            int count;
            while ((count = in.read(buffer)) != -1) out.write(buffer, 0, count);
            return out.toString(StandardCharsets.UTF_8.name());
        }
    }

    private void buildShell() {
        root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(BG);
        root.setPadding(dp(18), 0, dp(18), 0);

        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.VERTICAL);
        header.setPadding(0, dp(14), 0, dp(10));
        TextView title = text("Lesson Listener", 23, TEXT, true);
        header.addView(title);
        TextView subtitle = text("Помощник для учёбы · офлайн-интерфейс", 13, MUTED, false);
        LinearLayout.LayoutParams subLp = new LinearLayout.LayoutParams(-1, -2);
        subLp.topMargin = dp(2);
        header.addView(subtitle, subLp);
        globalStatus = text(statusText, 12, ACCENT, false);
        globalStatus.setMaxLines(2);
        LinearLayout.LayoutParams statusLp = new LinearLayout.LayoutParams(-1, -2);
        statusLp.topMargin = dp(7);
        header.addView(globalStatus, statusLp);
        root.addView(header);

        LinearLayout tabs = new LinearLayout(this);
        tabs.setOrientation(LinearLayout.HORIZONTAL);
        tabs.setPadding(dp(4), dp(4), dp(4), dp(4));
        tabs.setBackground(rounded(SURFACE, 22));
        lessonTabButton = tabButton("Урок", "lesson");
        notesTabButton = tabButton("Заметки", "notes");
        settingsTabButton = tabButton("Настройки", "settings");
        tabs.addView(lessonTabButton, new LinearLayout.LayoutParams(0, dp(44), 1));
        tabs.addView(notesTabButton, new LinearLayout.LayoutParams(0, dp(44), 1));
        tabs.addView(settingsTabButton, new LinearLayout.LayoutParams(0, dp(44), 1));
        root.addView(tabs);

        contentFrame = new FrameLayout(this);
        LinearLayout.LayoutParams contentLp = new LinearLayout.LayoutParams(-1, 0, 1);
        contentLp.topMargin = dp(8);
        root.addView(contentFrame, contentLp);
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            if (Build.VERSION.SDK_INT >= 30) {
                android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
                view.setPadding(dp(18) + bars.left, bars.top, dp(18) + bars.right, bars.bottom);
            } else {
                view.setPadding(dp(18), insets.getSystemWindowInsetTop(), dp(18), insets.getSystemWindowInsetBottom());
            }
            return insets;
        });
        setContentView(root);
        if (Build.VERSION.SDK_INT >= 30) getWindow().setDecorFitsSystemWindows(false);
    }

    private TextView tabButton(String label, String tab) {
        TextView view = text(label, 14, MUTED, true);
        view.setGravity(Gravity.CENTER);
        view.setBackground(rounded(SURFACE, 18));
        view.setOnClickListener(v -> {
            if (recording && !"lesson".equals(tab)) {
                Toast.makeText(this, "Завершите запись, чтобы сменить вкладку.", Toast.LENGTH_SHORT).show();
                return;
            }
            saveLessonDraft();
            currentTab = tab;
            renderCurrentTab();
        });
        return view;
    }

    private void renderCurrentTab() {
        if (contentFrame == null) return;
        lessonTabButton.setBackground(rounded("lesson".equals(currentTab) ? SURFACE_ALT : SURFACE, 18));
        notesTabButton.setBackground(rounded("notes".equals(currentTab) ? SURFACE_ALT : SURFACE, 18));
        settingsTabButton.setBackground(rounded("settings".equals(currentTab) ? SURFACE_ALT : SURFACE, 18));
        lessonTabButton.setTextColor("lesson".equals(currentTab) ? ACCENT : MUTED);
        notesTabButton.setTextColor("notes".equals(currentTab) ? ACCENT : MUTED);
        settingsTabButton.setTextColor("settings".equals(currentTab) ? ACCENT : MUTED);
        contentFrame.removeAllViews();
        if ("notes".equals(currentTab)) renderNotes();
        else if ("settings".equals(currentTab)) renderSettings();
        else renderLesson();
        updateGlobalStatus();
    }

    private void renderLesson() {
        ScrollView scroll = scrollView();
        LinearLayout body = column();
        scroll.addView(body);

        LinearLayout subjectCard = card();
        subjectCard.addView(label("ПРЕДМЕТ", 11, ACCENT));
        Spinner subjects = new Spinner(this);
        List<String> subjectIds = new ArrayList<>(profiles.keySet());
        List<String> subjectNames = new ArrayList<>();
        for (String id : subjectIds) subjectNames.add(profiles.get(id).optString("name", id));
        subjects.setAdapter(spinnerAdapter(subjectNames));
        int selected = Math.max(0, subjectIds.indexOf(activeSubject));
        subjects.setSelection(selected);
        LinearLayout.LayoutParams spinnerLp = new LinearLayout.LayoutParams(-1, dp(52));
        spinnerLp.topMargin = dp(3);
        subjectCard.addView(subjects, spinnerLp);
        JSONObject profile = profiles.get(activeSubject);
        String teacher = profile == null ? "" : profile.optString("teacher_name", "");
        TextView teacherView = text(teacher.isEmpty() ? "Выберите предмет, чтобы настроить контекст." : "Викладач: " + teacher, 13, MUTED, false);
        teacherView.setMaxLines(2);
        subjectCard.addView(teacherView);
        LinearLayout subjectActions = row();
        Button meet = button("Открыть урок", false);
        meet.setOnClickListener(v -> openLessonLink());
        Button timetable = button(showSchedule ? "Скрыть расписание" : "Расписание", false);
        timetable.setOnClickListener(v -> {
            saveLessonDraft();
            showSchedule = !showSchedule;
            renderCurrentTab();
        });
        subjectActions.addView(meet, weightedLp(1, dp(46), dp(8)));
        subjectActions.addView(timetable, weightedLp(1, dp(46), 0));
        LinearLayout.LayoutParams actionLp = new LinearLayout.LayoutParams(-1, -2);
        actionLp.topMargin = dp(8);
        subjectCard.addView(subjectActions, actionLp);
        body.addView(subjectCard);

        subjects.setOnItemSelectedListener(new android.widget.AdapterView.OnItemSelectedListener() {
            private String prior = activeSubject;
            @Override public void onItemSelected(android.widget.AdapterView<?> parent, View view, int position, long id) {
                if (position < 0 || position >= subjectIds.size()) return;
                String next = subjectIds.get(position);
                if (next.equals(activeSubject)) return;
                saveLessonDraft();
                activeSubject = next;
                prefs.edit().putString("active_subject", activeSubject).apply();
                questionDraft = "";
                answer = "";
                answerQuestion = "";
                prefs.edit().remove("last_answer").remove("last_question").apply();
                renderCurrentTab();
            }
            @Override public void onNothingSelected(android.widget.AdapterView<?> parent) { }
        });

        if (showSchedule) {
            LinearLayout scheduleCard = card();
            scheduleCard.addView(label("НЕДЕЛЬНОЕ РАСПИСАНИЕ", 11, ACCENT));
            renderSchedule(scheduleCard);
            body.addView(scheduleCard);
        }

        LinearLayout contextCard = card();
        contextCard.addView(label("КОНТЕКСТ УРОКА", 11, ACCENT));
        TextView hint = text("Добавьте тему, формулы или заметки к этому предмету. Контекст хранится отдельно для каждого предмета на устройстве.", 13, MUTED, false);
        LinearLayout.LayoutParams hintLp = new LinearLayout.LayoutParams(-1, -2);
        hintLp.topMargin = dp(5);
        contextCard.addView(hint, hintLp);
        contextField = editField("Тема и дополнительный контекст", contexts.getString(activeSubject, ""), false);
        contextField.setMinLines(3);
        contextField.setMaxLines(6);
        LinearLayout.LayoutParams contextLp = new LinearLayout.LayoutParams(-1, -2);
        contextLp.topMargin = dp(10);
        contextCard.addView(contextField, contextLp);
        contextField.addTextChangedListener(watcher(value -> contexts.edit().putString(activeSubject, value).apply()));
        body.addView(contextCard);

        LinearLayout askCard = card();
        askCard.addView(label("ВОПРОС ИЛИ ЗАДАНИЕ", 11, ACCENT));
        questionField = editField("Напишите вопрос — сеть используется только после отправки", questionDraft, false);
        questionField.setMinLines(2);
        questionField.setMaxLines(4);
        LinearLayout.LayoutParams questionLp = new LinearLayout.LayoutParams(-1, -2);
        questionLp.topMargin = dp(8);
        askCard.addView(questionField, questionLp);
        questionField.addTextChangedListener(watcher(value -> questionDraft = value));

        LinearLayout actions = row();
        Button send = button("Спросить ИИ", true);
        send.setOnClickListener(v -> askText());
        Button photo = button("Выбрать фото", false);
        photo.setOnClickListener(v -> chooseImage());
        actions.addView(send, weightedLp(1, dp(48), dp(8)));
        actions.addView(photo, weightedLp(1, dp(48), 0));
        LinearLayout.LayoutParams actionsLp = new LinearLayout.LayoutParams(-1, -2);
        actionsLp.topMargin = dp(9);
        askCard.addView(actions, actionsLp);
        if (selectedImage != null) {
            TextView chosen = text("Фото: " + imageName, 13, MUTED, false);
            LinearLayout.LayoutParams chosenLp = new LinearLayout.LayoutParams(-1, -2);
            chosenLp.topMargin = dp(7);
            askCard.addView(chosen, chosenLp);
            Button analyzePhoto = button("Разобрать фото", true);
            analyzePhoto.setOnClickListener(v -> analyzeImage());
            LinearLayout.LayoutParams imageLp = new LinearLayout.LayoutParams(-1, dp(46));
            imageLp.topMargin = dp(7);
            askCard.addView(analyzePhoto, imageLp);
            Button clearPhoto = button("Убрать фото", false);
            clearPhoto.setOnClickListener(v -> {
                selectedImage = null;
                imageName = "";
                renderCurrentTab();
            });
            LinearLayout.LayoutParams clearLp = new LinearLayout.LayoutParams(-1, dp(40));
            clearLp.topMargin = dp(5);
            askCard.addView(clearPhoto, clearLp);
        }

        LinearLayout audioActions = row();
        Button microphone = button(recording ? "Остановить запись" : "Записать вопрос · до 45 с", false);
        microphone.setOnClickListener(v -> {
            if (recording) stopRecording(true);
            else requestMicrophone();
        });
        audioActions.addView(microphone, new LinearLayout.LayoutParams(0, dp(46), 1));
        LinearLayout.LayoutParams audioLp = new LinearLayout.LayoutParams(-1, -2);
        audioLp.topMargin = dp(7);
        askCard.addView(audioActions, audioLp);
        TextView audioNote = text("Запись с микрофона отправляется в Gemini только после остановки. На Android приложение не слушает другие приложения.", 12, MUTED, false);
        LinearLayout.LayoutParams audioNoteLp = new LinearLayout.LayoutParams(-1, -2);
        audioNoteLp.topMargin = dp(7);
        askCard.addView(audioNote, audioNoteLp);
        body.addView(askCard);

        LinearLayout resultCard = card();
        LinearLayout resultHeading = row();
        resultHeading.addView(label("ОТВЕТ И ПОЯСНЕНИЕ", 11, ACCENT), new LinearLayout.LayoutParams(0, -2, 1));
        progressBar = new ProgressBar(this, null, android.R.attr.progressBarStyleSmall);
        progressBar.setIndeterminate(true);
        progressBar.setVisibility(busy ? View.VISIBLE : View.GONE);
        resultHeading.addView(progressBar, new LinearLayout.LayoutParams(dp(22), dp(22)));
        resultCard.addView(resultHeading);
        lessonStatusView = text(statusText, 13, MUTED, false);
        LinearLayout.LayoutParams resultStatusLp = new LinearLayout.LayoutParams(-1, -2);
        resultStatusLp.topMargin = dp(6);
        resultCard.addView(lessonStatusView, resultStatusLp);
        answerView = text(answer.isEmpty() ? "Задайте вопрос или выберите фото — здесь появится пояснение." : answer, 15, TEXT, false);
        answerView.setTextIsSelectable(true);
        answerView.setLineSpacing(dp(3), 1f);
        LinearLayout.LayoutParams answerLp = new LinearLayout.LayoutParams(-1, -2);
        answerLp.topMargin = dp(11);
        resultCard.addView(answerView, answerLp);
        if (!answer.isEmpty()) {
            LinearLayout resultActions = row();
            Button save = button("Сохранить заметку", true);
            save.setOnClickListener(v -> saveAnswerAsNote());
            Button copy = button("Копировать", false);
            copy.setOnClickListener(v -> copyAnswer());
            resultActions.addView(save, weightedLp(1, dp(44), dp(8)));
            resultActions.addView(copy, weightedLp(1, dp(44), 0));
            LinearLayout.LayoutParams resultActionsLp = new LinearLayout.LayoutParams(-1, -2);
            resultActionsLp.topMargin = dp(10);
            resultCard.addView(resultActions, resultActionsLp);
        }
        if (busy) {
            Button cancel = button("Отменить запрос", false);
            cancel.setTextColor(DANGER);
            cancel.setOnClickListener(v -> cancelRequest());
            LinearLayout.LayoutParams cancelLp = new LinearLayout.LayoutParams(-1, dp(42));
            cancelLp.topMargin = dp(8);
            resultCard.addView(cancel, cancelLp);
        }
        body.addView(resultCard);
        contentFrame.addView(scroll);
        updateGlobalStatus();
    }

    private void renderSchedule(LinearLayout target) {
        JSONObject timetable = schedule.optJSONObject("timetable");
        JSONArray bells = schedule.optJSONArray("bells");
        if (timetable == null) {
            target.addView(text("Расписание недоступно в комплекте приложения.", 13, MUTED, false));
            return;
        }
        String[] days = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"};
        String[] dayLabels = {"Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"};
        for (int d = 0; d < days.length; d++) {
            JSONArray lessons = timetable.optJSONArray(days[d]);
            if (lessons == null || lessons.length() == 0) continue;
            List<String> entries = new ArrayList<>();
            for (int i = 0; i < lessons.length(); i++) {
                String id = lessons.optString(i, "");
                if (id.isEmpty()) continue;
                JSONObject profile = profiles.get(id);
                String name = profile == null ? id : profile.optString("name", id);
                JSONObject bell = bells != null && i < bells.length() ? bells.optJSONObject(i) : null;
                String time = bell == null ? "" : bell.optString("start", "") + "–" + bell.optString("end", "");
                entries.add((i + 1) + ". " + time + "  " + name);
            }
            if (entries.isEmpty()) continue;
            TextView day = text(dayLabels[d], 14, TEXT, true);
            LinearLayout.LayoutParams dayLp = new LinearLayout.LayoutParams(-1, -2);
            dayLp.topMargin = dp(10);
            target.addView(day, dayLp);
            target.addView(text(android.text.TextUtils.join("\n", entries), 13, MUTED, false));
        }
    }

    private void renderNotes() {
        ScrollView scroll = scrollView();
        LinearLayout body = column();
        scroll.addView(body);
        LinearLayout intro = card();
        intro.addView(label("ЛОКАЛЬНЫЕ ЗАМЕТКИ", 11, ACCENT));
        TextView copy = text("Сохраняйте ответы с урока на устройстве и экспортируйте их в выбранный файл.", 14, MUTED, false);
        LinearLayout.LayoutParams copyLp = new LinearLayout.LayoutParams(-1, -2);
        copyLp.topMargin = dp(6);
        intro.addView(copy, copyLp);
        Button export = button("Экспортировать заметки", true);
        export.setOnClickListener(v -> exportNotes());
        LinearLayout.LayoutParams exportLp = new LinearLayout.LayoutParams(-1, dp(46));
        exportLp.topMargin = dp(10);
        intro.addView(export, exportLp);
        body.addView(intro);
        JSONArray notes = getNotes();
        if (notes.length() == 0) {
            LinearLayout empty = card();
            empty.addView(text("Пока нет сохранённых заметок. После ответа нажмите «Сохранить заметку» на вкладке «Урок».", 14, MUTED, false));
            body.addView(empty);
        } else {
            for (int i = notes.length() - 1; i >= 0; i--) {
                JSONObject note = notes.optJSONObject(i);
                if (note == null) continue;
                LinearLayout noteCard = card();
                String heading = note.optString("subject", "Урок") + " · " + note.optString("date", "");
                noteCard.addView(label(heading, 11, ACCENT));
                String q = note.optString("question", "");
                if (!q.isEmpty()) {
                    TextView qView = text("Вопрос: " + q, 14, TEXT, true);
                    LinearLayout.LayoutParams qLp = new LinearLayout.LayoutParams(-1, -2);
                    qLp.topMargin = dp(7);
                    noteCard.addView(qView, qLp);
                }
                TextView aView = text(note.optString("answer", ""), 14, MUTED, false);
                aView.setTextIsSelectable(true);
                LinearLayout.LayoutParams aLp = new LinearLayout.LayoutParams(-1, -2);
                aLp.topMargin = dp(6);
                noteCard.addView(aView, aLp);
                body.addView(noteCard);
            }
        }
        contentFrame.addView(scroll);
    }

    private void renderSettings() {
        ScrollView scroll = scrollView();
        LinearLayout body = column();
        scroll.addView(body);
        LinearLayout card = card();
        card.addView(label("ПРОВАЙДЕР И МОДЕЛЬ", 11, ACCENT));
        TextView intro = text("Ключи хранятся в закрытом хранилище приложения и исключены из резервных копий. Они не отправляются, пока вы явно не запустите запрос.", 13, MUTED, false);
        LinearLayout.LayoutParams introLp = new LinearLayout.LayoutParams(-1, -2);
        introLp.topMargin = dp(6);
        card.addView(intro, introLp);

        providerSpinner = new Spinner(this);
        String[] providers = {"Gemini", "OpenRouter", "Groq"};
        providerSpinner.setAdapter(spinnerAdapter(java.util.Arrays.asList(providers)));
        providerSpinner.setSelection(providerIndex(activeProvider));
        LinearLayout.LayoutParams providerLp = new LinearLayout.LayoutParams(-1, dp(50));
        providerLp.topMargin = dp(8);
        card.addView(providerSpinner, providerLp);
        providerSpinner.setOnItemSelectedListener(new android.widget.AdapterView.OnItemSelectedListener() {
            @Override public void onItemSelected(android.widget.AdapterView<?> parent, View view, int position, long id) {
                String next = providerAt(position);
                if (!next.equals(activeProvider)) {
                    activeProvider = next;
                    if (modelField != null) modelField.setText(modelFor(activeProvider));
                    refreshAudioProviderHint();
                }
            }
            @Override public void onNothingSelected(android.widget.AdapterView<?> parent) { }
        });

        TextView modelLabel = label("Модель (можно изменить)", 12, MUTED);
        LinearLayout.LayoutParams modelLabelLp = new LinearLayout.LayoutParams(-1, -2);
        modelLabelLp.topMargin = dp(8);
        card.addView(modelLabel, modelLabelLp);
        modelField = editField("Например, gemini-2.5-flash", modelFor(activeProvider), false);
        LinearLayout.LayoutParams modelLp = new LinearLayout.LayoutParams(-1, dp(52));
        modelLp.topMargin = dp(5);
        card.addView(modelField, modelLp);

        TextView keyLabel = label("API-ключ", 12, MUTED);
        LinearLayout.LayoutParams keyLabelLp = new LinearLayout.LayoutParams(-1, -2);
        keyLabelLp.topMargin = dp(10);
        card.addView(keyLabel, keyLabelLp);
        keyField = editField("Вставьте ключ · пустое поле оставит сохранённый", "", true);
        LinearLayout.LayoutParams keyLp = new LinearLayout.LayoutParams(-1, dp(52));
        keyLp.topMargin = dp(5);
        card.addView(keyField, keyLp);
        TextView keyState = text(hasKey(activeProvider) ? "Для этого провайдера ключ уже сохранён." : "Для работы добавьте ключ выбранного провайдера.", 12, MUTED, false);
        LinearLayout.LayoutParams keyStateLp = new LinearLayout.LayoutParams(-1, -2);
        keyStateLp.topMargin = dp(6);
        card.addView(keyState, keyStateLp);

        Button save = button("Сохранить настройки", true);
        save.setOnClickListener(v -> saveSettings(modelField.getText().toString(), keyField.getText().toString()));
        LinearLayout.LayoutParams saveLp = new LinearLayout.LayoutParams(-1, dp(46));
        saveLp.topMargin = dp(10);
        card.addView(save, saveLp);
        Button clearKey = button("Удалить сохранённый ключ", false);
        clearKey.setTextColor(DANGER);
        clearKey.setOnClickListener(v -> {
            prefs.edit().remove(keyPreference(activeProvider)).apply();
            keyField.setText("");
            keyState.setText("Ключ удалён с устройства.");
            setStatus("API-ключ удалён с устройства.", MUTED);
        });
        LinearLayout.LayoutParams clearLp = new LinearLayout.LayoutParams(-1, dp(42));
        clearLp.topMargin = dp(6);
        card.addView(clearKey, clearLp);
        body.addView(card);

        LinearLayout details = card();
        details.addView(label("ПРИВАТНОСТЬ И ОГРАНИЧЕНИЯ", 11, ACCENT));
        details.addView(text("Запросы отправляются напрямую выбранному AI-провайдеру только после нажатия кнопки. API-ключи не выводятся на экран и не записываются в журнал. Текст и ответы хранятся локально.", 13, MUTED, false));
        details.addView(text("Запись работает только с микрофоном после системного разрешения. Android-версия не захватывает звук других приложений, не поддерживает глобальные горячие клавиши, плавающую поверх окон панель и автозапуск Windows.", 13, MUTED, false));
        body.addView(details);
        contentFrame.addView(scroll);
    }

    private void refreshAudioProviderHint() {
        if (lessonStatusView != null && !recording) {
            lessonStatusView.setText(activeProvider.equals("gemini")
                    ? statusText
                    : "Анализ аудио доступен через Gemini. Выберите Gemini в настройках.");
        }
    }

    private int providerIndex(String provider) {
        if ("openrouter".equals(provider)) return 1;
        if ("groq".equals(provider)) return 2;
        return 0;
    }

    private String providerAt(int position) {
        return position == 1 ? "openrouter" : position == 2 ? "groq" : "gemini";
    }

    private String providerName(String provider) {
        if ("openrouter".equals(provider)) return "OpenRouter";
        if ("groq".equals(provider)) return "Groq";
        return "Gemini";
    }

    private String keyPreference(String provider) { return "api_key_" + provider; }

    private String modelPreference(String provider) { return "model_" + provider; }

    private String modelDefault(String provider) {
        if ("openrouter".equals(provider)) return "google/gemini-2.5-flash";
        if ("groq".equals(provider)) return "llama-3.3-70b-versatile";
        return "gemini-2.5-flash";
    }

    private String modelFor(String provider) {
        return settings.getString(modelPreference(provider), modelDefault(provider));
    }

    private boolean hasKey(String provider) {
        return !prefs.getString(keyPreference(provider), "").isEmpty();
    }

    private void saveSettings(String model, String key) {
        String cleanModel = model.trim();
        if (cleanModel.isEmpty()) {
            setStatus("Укажите название модели.", DANGER);
            return;
        }
        settings.edit().putString(modelPreference(activeProvider), cleanModel).apply();
        SharedPreferences.Editor editor = prefs.edit().putString("active_provider", activeProvider);
        String cleanKey = key.trim();
        if (!cleanKey.isEmpty()) editor.putString(keyPreference(activeProvider), cleanKey);
        editor.apply();
        keyField.setText("");
        currentTab = "settings";
        setStatus("Настройки " + providerName(activeProvider) + " сохранены на устройстве.", ACCENT);
        renderCurrentTab();
        Toast.makeText(this, "Настройки сохранены", Toast.LENGTH_SHORT).show();
    }

    private void saveLessonDraft() {
        if (contextField != null) contexts.edit().putString(activeSubject, contextField.getText().toString()).apply();
        if (questionField != null) questionDraft = questionField.getText().toString();
    }

    private String promptContext() {
        JSONObject profile = profiles.get(activeSubject);
        StringBuilder context = new StringBuilder();
        if (profile != null) {
            context.append("Предмет: ").append(profile.optString("name", activeSubject)).append('\n');
            String teacher = profile.optString("teacher_name", "");
            if (!teacher.isEmpty()) context.append("Викладач: ").append(teacher).append('\n');
            String style = profile.optString("teacher_style", "");
            if (!style.isEmpty()) context.append("Стиль: ").append(style).append('\n');
            JSONArray rules = profile.optJSONArray("permanent_rules");
            if (rules != null && rules.length() > 0) context.append("Правила: ").append(joinArray(rules, "; ")).append('\n');
            JSONArray past = profile.optJSONArray("past_lessons");
            if (past != null && past.length() > 0) {
                context.append("Архив прошлых уроков: ");
                for (int i = Math.max(0, past.length() - 3); i < past.length(); i++) {
                    JSONObject lesson = past.optJSONObject(i);
                    if (lesson != null) context.append(lesson.optString("topic", "")).append(" — ").append(lesson.optString("summary", "")).append("; ");
                }
                context.append('\n');
            }
        }
        String notes = contexts.getString(activeSubject, "").trim();
        if (!notes.isEmpty()) context.append("Контекст ученика: ").append(notes).append('\n');
        return context.toString();
    }

    private String joinArray(JSONArray values, String separator) {
        List<String> parts = new ArrayList<>();
        for (int i = 0; i < values.length(); i++) parts.add(values.optString(i, ""));
        return android.text.TextUtils.join(separator, parts);
    }

    private boolean isRussian() {
        String id = activeSubject.toLowerCase(Locale.ROOT);
        String name = profiles.containsKey(activeSubject) ? profiles.get(activeSubject).optString("name", "") : "";
        return id.contains("rus") || id.endsWith("_ru") || name.contains("рус") || name.contains("росси");
    }

    private String studyPrompt(String question, boolean vision, boolean audio) {
        boolean russian = isRussian();
        String lang = russian ? "русском" : "українською";
        String task;
        if (vision) {
            task = russian
                    ? "Реши задание на прикреплённом фото. Если есть варианты, назови верный вариант; если это расчёт — покажи короткие шаги."
                    : "Розв'яжи завдання на прикріпленому фото. Якщо є варіанти, назви правильний варіант; якщо це обчислення — покажи короткі кроки.";
        } else if (audio) {
            task = russian
                    ? "Прослушай короткую запись урока. Определи вопрос или задание преподавателя и объясни ответ. Если вопрос неразборчив, честно укажи это."
                    : "Прослухай короткий запис уроку. Визнач запитання або завдання викладача й поясни відповідь. Якщо запитання нерозбірливе — чесно вкажи це.";
        } else {
            task = russian
                    ? "Ответь на вопрос ученика и объясни решение конкретными шагами."
                    : "Дай відповідь на запитання учня й поясни розв'язання конкретними кроками.";
        }
        return "Ти — уважний помічник учня під час уроку. " + task + "\n"
                + "Відповідай " + lang + ". Спершу коротка пряма відповідь, далі зрозуміле покрокове пояснення. Не вигадуй дані, якщо умова нечітка. "
                + "Формули оформлюй читабельно, підсумуй важливі терміни.\n\n"
                + "КОНТЕКСТ ПРЕДМЕТА:\n" + promptContext()
                + (question == null || question.trim().isEmpty() ? "" : "\nПИТАННЯ УЧНЯ:\n" + question.trim());
    }

    private void askText() {
        saveLessonDraft();
        String question = questionDraft.trim();
        if (question.isEmpty()) {
            setStatus("Сначала напишите вопрос.", DANGER);
            return;
        }
        ensureConfigured();
        if (!hasKey(activeProvider)) return;
        final String prompt = studyPrompt(question, false, false);
        runRequest("Готовлю пояснение…", () -> sendTextRequest(activeProvider, modelFor(activeProvider), prompt));
    }

    private void ensureConfigured() {
        if (modelFor(activeProvider).trim().isEmpty()) setStatus("Укажите модель в настройках.", DANGER);
        else if (!hasKey(activeProvider)) setStatus("Добавьте API-ключ " + providerName(activeProvider) + " в настройках.", DANGER);
    }

    private void runRequest(String message, RequestWork work) {
        if (busy || destroyed) return;
        busy = true;
        setStatus(message, ACCENT);
        renderCurrentTab();
        activeTask = networkExecutor.submit(() -> {
            try {
                String result = work.run();
                if (Thread.currentThread().isInterrupted()) return;
                mainHandler.post(() -> {
                    if (destroyed) return;
                    busy = false;
                    activeTask = null;
                    answer = result == null || result.trim().isEmpty() ? "Провайдер вернул пустой ответ. Попробуйте ещё раз." : result.trim();
                    answerQuestion = questionDraft;
                    prefs.edit().putString("last_answer", answer).putString("last_question", answerQuestion).apply();
                    setStatus("Ответ готов · " + providerName(activeProvider), ACCENT);
                    renderCurrentTab();
                });
            } catch (Exception error) {
                if (Thread.currentThread().isInterrupted()) return;
                String friendly = friendlyError(error);
                mainHandler.post(() -> {
                    if (destroyed) return;
                    busy = false;
                    activeTask = null;
                    setStatus(friendly, DANGER);
                    renderCurrentTab();
                });
            }
        });
    }

    private interface RequestWork { String run() throws Exception; }

    private String sendTextRequest(String provider, String model, String prompt) throws Exception {
        if ("gemini".equals(provider)) return callGemini(model, prompt, null, "");
        return callChatCompletion(provider, model, prompt, null);
    }

    private void chooseImage() {
        Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType("image/*");
        startActivityForResult(intent, REQ_IMAGE);
    }

    private void analyzeImage() {
        saveLessonDraft();
        if (selectedImage == null) return;
        if ("groq".equals(activeProvider)) {
            setStatus("Groq не поддерживает анализ изображений в этом приложении. Выберите Gemini или OpenRouter.", DANGER);
            return;
        }
        if (!hasKey(activeProvider)) {
            setStatus("Добавьте API-ключ " + providerName(activeProvider) + " в настройках.", DANGER);
            return;
        }
        final Uri image = selectedImage;
        final String prompt = studyPrompt(questionDraft, true, false);
        final String provider = activeProvider;
        final String model = modelFor(provider);
        runRequest("Подготавливаю фото задания…", () -> {
            byte[] bytes = readAndResizeImage(image);
            if ("gemini".equals(provider)) return callGemini(model, prompt, bytes, "image/jpeg");
            return callChatCompletion(provider, model, prompt, bytes);
        });
    }

    private byte[] readAndResizeImage(Uri uri) throws Exception {
        ContentResolver resolver = getContentResolver();
        byte[] original;
        try (InputStream in = resolver.openInputStream(uri); ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            if (in == null) throw new IOException("image");
            byte[] buffer = new byte[8192];
            int count;
            while ((count = in.read(buffer)) != -1) {
                if (out.size() + count > MAX_IMAGE_BYTES) throw new ApiException("Фото слишком большое. Выберите изображение меньше 12 МБ.");
                out.write(buffer, 0, count);
            }
            original = out.toByteArray();
        }
        BitmapFactory.Options bounds = new BitmapFactory.Options();
        bounds.inJustDecodeBounds = true;
        BitmapFactory.decodeByteArray(original, 0, original.length, bounds);
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) throw new ApiException("Не удалось прочитать это изображение.");
        int sample = 1;
        while (bounds.outWidth / sample > 2048 || bounds.outHeight / sample > 2048) sample *= 2;
        BitmapFactory.Options options = new BitmapFactory.Options();
        options.inSampleSize = sample;
        Bitmap bitmap = BitmapFactory.decodeByteArray(original, 0, original.length, options);
        if (bitmap == null) throw new ApiException("Не удалось открыть изображение.");
        try (ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            bitmap.compress(Bitmap.CompressFormat.JPEG, 84, out);
            return out.toByteArray();
        } finally {
            bitmap.recycle();
        }
    }

    private void requestMicrophone() {
        if (!"gemini".equals(activeProvider)) {
            setStatus("Аудиоанализ поддерживается только через Gemini. Выберите Gemini в настройках.", DANGER);
            return;
        }
        if (!hasKey("gemini")) {
            setStatus("Добавьте API-ключ Gemini в настройках.", DANGER);
            return;
        }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, REQ_MIC);
            return;
        }
        startRecording();
    }

    private void startRecording() {
        if (recording || busy) return;
        try {
            audioFile = File.createTempFile("lesson-audio-", ".m4a", getCacheDir());
            if (Build.VERSION.SDK_INT >= 31) recorder = new MediaRecorder(this);
            else recorder = new MediaRecorder();
            recorder.setAudioSource(MediaRecorder.AudioSource.MIC);
            recorder.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4);
            recorder.setAudioEncoder(MediaRecorder.AudioEncoder.AAC);
            recorder.setAudioSamplingRate(16000);
            recorder.setAudioEncodingBitRate(64000);
            recorder.setOutputFile(audioFile.getAbsolutePath());
            recorder.prepare();
            recorder.start();
            recording = true;
            recordingStartedAt = System.currentTimeMillis();
            setStatus("● Идёт запись с микрофона · 00:00 / 00:45", DANGER);
            renderCurrentTab();
            recordingTicker = new Runnable() {
                @Override public void run() {
                    if (!recording) return;
                    long seconds = Math.max(0, (System.currentTimeMillis() - recordingStartedAt) / 1000);
                    setStatus(String.format(Locale.getDefault(), "● Идёт запись с микрофона · 00:%02d / 00:45", seconds), DANGER);
                    if (seconds >= MAX_AUDIO_SECONDS) stopRecording(true);
                    else mainHandler.postDelayed(this, 500);
                }
            };
            mainHandler.postDelayed(recordingTicker, 500);
        } catch (Exception e) {
            releaseRecorder();
            recording = false;
            setStatus("Не удалось запустить микрофон. Проверьте разрешение и подключённый микрофон.", DANGER);
        }
    }

    private void stopRecording(boolean analyze) {
        if (!recording) return;
        recording = false;
        if (recordingTicker != null) mainHandler.removeCallbacks(recordingTicker);
        boolean saved = true;
        try {
            if (recorder != null) recorder.stop();
        } catch (RuntimeException e) {
            saved = false;
        }
        releaseRecorder();
        renderCurrentTab();
        if (!saved || audioFile == null || !audioFile.exists() || audioFile.length() < 256) {
            deleteAudio();
            setStatus("Запись слишком короткая или не удалась. Попробуйте записать ещё раз.", DANGER);
            return;
        }
        if (!analyze) {
            deleteAudio();
            setStatus("Запись удалена.", MUTED);
            return;
        }
        if (!hasKey("gemini")) {
            deleteAudio();
            setStatus("Добавьте API-ключ Gemini в настройках.", DANGER);
            return;
        }
        String prompt = studyPrompt(questionDraft, false, true);
        String model = modelFor("gemini");
        File captured = audioFile;
        runRequest("Обрабатываю запись через Gemini…", () -> {
            byte[] bytes = readLimitedFile(captured, MAX_AUDIO_SECONDS * 128 * 1024);
            try {
                return callGemini(model, prompt, bytes, "audio/mp4");
            } finally {
                captured.delete();
            }
        });
        audioFile = null;
    }

    private byte[] readLimitedFile(File file, int limit) throws IOException, ApiException {
        if (file.length() > limit) throw new ApiException("Аудиофайл слишком большой. Запишите короткий фрагмент.");
        try (InputStream in = new java.io.FileInputStream(file); ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[8192];
            int count;
            while ((count = in.read(buffer)) != -1) {
                if (out.size() + count > limit) throw new ApiException("Аудиофайл слишком большой.");
                out.write(buffer, 0, count);
            }
            return out.toByteArray();
        }
    }

    private void releaseRecorder() {
        if (recorder != null) {
            try { recorder.reset(); } catch (Exception ignored) { }
            try { recorder.release(); } catch (Exception ignored) { }
            recorder = null;
        }
    }

    private void deleteAudio() {
        if (audioFile != null) audioFile.delete();
        audioFile = null;
    }

    private String callGemini(String model, String prompt, byte[] media, String mimeType) throws Exception {
        String key = prefs.getString(keyPreference("gemini"), "");
        if (key.isEmpty()) throw new ApiException("Добавьте API-ключ Gemini в настройках.");
        JSONObject payload = new JSONObject();
        JSONArray parts = new JSONArray();
        if (media != null) {
            JSONObject inline = new JSONObject();
            inline.put("mimeType", mimeType);
            inline.put("data", Base64.encodeToString(media, Base64.NO_WRAP));
            JSONObject part = new JSONObject();
            part.put("inlineData", inline);
            parts.put(part);
        }
        parts.put(new JSONObject().put("text", prompt));
        payload.put("contents", new JSONArray().put(new JSONObject().put("parts", parts)));
        payload.put("generationConfig", new JSONObject().put("temperature", 0.2).put("maxOutputTokens", 1800));
        String url = "https://generativelanguage.googleapis.com/v1beta/models/" + Uri.encode(model) + ":generateContent?key=" + Uri.encode(key);
        JSONObject response = postJson(url, null, payload);
        JSONArray candidates = response.optJSONArray("candidates");
        if (candidates == null || candidates.length() == 0) throw new ApiException("Gemini не вернул ответ. Проверьте модель и формат задания.");
        JSONObject content = candidates.optJSONObject(0).optJSONObject("content");
        JSONArray responseParts = content == null ? null : content.optJSONArray("parts");
        StringBuilder answerText = new StringBuilder();
        if (responseParts != null) {
            for (int i = 0; i < responseParts.length(); i++) {
                JSONObject part = responseParts.optJSONObject(i);
                if (part != null && part.has("text")) {
                    if (answerText.length() > 0) answerText.append('\n');
                    answerText.append(part.optString("text", ""));
                }
            }
        }
        if (answerText.length() == 0) throw new ApiException("Gemini вернул пустой ответ. Попробуйте ещё раз.");
        return answerText.toString();
    }

    private String callChatCompletion(String provider, String model, String prompt, byte[] image) throws Exception {
        String key = prefs.getString(keyPreference(provider), "");
        if (key.isEmpty()) throw new ApiException("Добавьте API-ключ " + providerName(provider) + " в настройках.");
        if (image != null && "groq".equals(provider)) throw new ApiException("Groq не поддерживает изображения в этой версии. Выберите Gemini или OpenRouter.");
        String url = "openrouter".equals(provider)
                ? "https://openrouter.ai/api/v1/chat/completions"
                : "https://api.groq.com/openai/v1/chat/completions";
        JSONObject payload = new JSONObject().put("model", model).put("temperature", 0.2).put("max_tokens", 1800);
        JSONObject message = new JSONObject().put("role", "user");
        if (image == null) {
            message.put("content", prompt);
        } else {
            JSONArray content = new JSONArray();
            content.put(new JSONObject().put("type", "text").put("text", prompt));
            String dataUrl = "data:image/jpeg;base64," + Base64.encodeToString(image, Base64.NO_WRAP);
            content.put(new JSONObject().put("type", "image_url").put("image_url", new JSONObject().put("url", dataUrl)));
            message.put("content", content);
        }
        payload.put("messages", new JSONArray().put(message));
        Map<String, String> headers = new LinkedHashMap<>();
        headers.put("Authorization", "Bearer " + key);
        if ("openrouter".equals(provider)) {
            headers.put("HTTP-Referer", "https://lesson-listener.local");
            headers.put("X-Title", "Lesson Listener Android");
        }
        JSONObject response = postJson(url, headers, payload);
        JSONArray choices = response.optJSONArray("choices");
        JSONObject choice = choices == null ? null : choices.optJSONObject(0);
        JSONObject messageResult = choice == null ? null : choice.optJSONObject("message");
        String content = messageResult == null ? "" : messageResult.optString("content", "");
        if (content.isEmpty()) throw new ApiException(providerName(provider) + " вернул пустой ответ.");
        return content;
    }

    private JSONObject postJson(String endpoint, Map<String, String> headers, JSONObject payload) throws Exception {
        checkCancelled();
        HttpURLConnection connection = (HttpURLConnection) new URL(endpoint).openConnection();
        activeConnection = connection;
        try {
            connection.setRequestMethod("POST");
            connection.setConnectTimeout(15000);
            connection.setReadTimeout(60000);
            connection.setDoOutput(true);
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
            if (headers != null) for (Map.Entry<String, String> header : headers.entrySet()) connection.setRequestProperty(header.getKey(), header.getValue());
            byte[] body = payload.toString().getBytes(StandardCharsets.UTF_8);
            connection.setFixedLengthStreamingMode(body.length);
            try (OutputStream output = connection.getOutputStream()) { output.write(body); }
            checkCancelled();
            int status = connection.getResponseCode();
            InputStream stream = status >= 400 ? connection.getErrorStream() : connection.getInputStream();
            String responseText = stream == null ? "{}" : readResponse(stream);
            checkCancelled();
            if (status < 200 || status >= 300) throw new ApiException(httpError(status));
            try { return new JSONObject(responseText); }
            catch (JSONException e) { throw new ApiException("Провайдер вернул неожиданный ответ. Попробуйте ещё раз."); }
        } catch (SocketTimeoutException e) {
            throw new ApiException("Истекло время ожидания ответа. Проверьте интернет и повторите запрос.");
        } finally {
            connection.disconnect();
            if (activeConnection == connection) activeConnection = null;
        }
    }

    private String readResponse(InputStream input) throws IOException, ApiException {
        try (InputStream in = input; ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[8192];
            int count;
            while ((count = in.read(buffer)) != -1) {
                if (out.size() + count > MAX_HTTP_BYTES) throw new ApiException("Ответ провайдера слишком велик.");
                out.write(buffer, 0, count);
            }
            return out.toString(StandardCharsets.UTF_8.name());
        }
    }

    private String httpError(int status) {
        if (status == 400) return "Провайдер отклонил запрос. Проверьте выбранную модель и формат задания.";
        if (status == 401 || status == 403) return "Провайдер не принял API-ключ или доступ к модели ограничен. Проверьте ключ.";
        if (status == 404) return "Модель не найдена. Укажите актуальное название модели в настройках.";
        if (status == 413) return "Запрос слишком большой. Выберите изображение меньшего размера.";
        if (status == 429) return "Лимит запросов провайдера исчерпан. Подождите или проверьте тариф и квоту.";
        if (status >= 500) return "Сервис провайдера временно недоступен. Попробуйте позже.";
        return "Провайдер вернул HTTP " + status + ". Проверьте настройки и повторите запрос.";
    }

    private void checkCancelled() throws InterruptedException {
        if (Thread.currentThread().isInterrupted()) throw new InterruptedException("cancelled");
    }

    private String friendlyError(Exception error) {
        if (error instanceof ApiException) return error.getMessage();
        if (error instanceof SocketTimeoutException) return "Истекло время ожидания ответа. Проверьте интернет и повторите запрос.";
        if (error instanceof IOException) return "Не удалось подключиться к провайдеру. Проверьте интернет и повторите запрос.";
        return "Не удалось обработать запрос. Проверьте настройки провайдера и попробуйте ещё раз.";
    }

    private static final class ApiException extends Exception {
        ApiException(String message) { super(message); }
    }

    private void cancelRequest() {
        Future<?> task = activeTask;
        if (task != null) task.cancel(true);
        HttpURLConnection connection = activeConnection;
        if (connection != null) connection.disconnect();
        activeTask = null;
        busy = false;
        setStatus("Запрос отменён.", MUTED);
        renderCurrentTab();
    }

    private void openLessonLink() {
        JSONObject profile = profiles.get(activeSubject);
        String link = profile == null ? "" : profile.optString("meet_url", "").trim();
        if (link.isEmpty()) {
            setStatus("Для этого предмета ссылка на онлайн-урок не задана.", MUTED);
            return;
        }
        try {
            Intent intent = new Intent(Intent.ACTION_VIEW, Uri.parse(link));
            startActivity(Intent.createChooser(intent, "Открыть ссылку на урок"));
        } catch (Exception e) {
            setStatus("Не найдено приложение для открытия ссылки.", DANGER);
        }
    }

    private JSONArray getNotes() {
        try { return new JSONArray(prefs.getString("lesson_notes", "[]")); }
        catch (JSONException e) { return new JSONArray(); }
    }

    private void saveAnswerAsNote() {
        if (answer.isEmpty()) return;
        JSONArray notes = getNotes();
        JSONObject note = new JSONObject();
        try {
            JSONObject profile = profiles.get(activeSubject);
            note.put("date", new SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.getDefault()).format(new Date()));
            note.put("subject", profile == null ? activeSubject : profile.optString("name", activeSubject));
            note.put("question", answerQuestion);
            note.put("answer", answer);
            notes.put(note);
            while (notes.length() > 200) {
                JSONArray trimmed = new JSONArray();
                for (int i = 1; i < notes.length(); i++) trimmed.put(notes.get(i));
                notes = trimmed;
            }
            prefs.edit().putString("lesson_notes", notes.toString()).apply();
            setStatus("Ответ сохранён в заметки на устройстве.", ACCENT);
            Toast.makeText(this, "Заметка сохранена", Toast.LENGTH_SHORT).show();
        } catch (JSONException e) {
            setStatus("Не удалось сохранить заметку.", DANGER);
        }
    }

    private void copyAnswer() {
        ClipboardManager clipboard = (ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
        clipboard.setPrimaryClip(ClipData.newPlainText("Lesson Listener", answer));
        Toast.makeText(this, "Ответ скопирован", Toast.LENGTH_SHORT).show();
    }

    private void exportNotes() {
        Intent intent = new Intent(Intent.ACTION_CREATE_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType("text/markdown");
        intent.putExtra(Intent.EXTRA_TITLE, "lesson-listener-notes.md");
        exportTarget = null;
        startActivityForResult(intent, REQ_EXPORT);
    }

    private String notesMarkdown() {
        JSONArray notes = getNotes();
        StringBuilder out = new StringBuilder("# Lesson Listener — заметки уроков\n\n");
        for (int i = 0; i < notes.length(); i++) {
            JSONObject note = notes.optJSONObject(i);
            if (note == null) continue;
            out.append("## ").append(note.optString("subject", "Урок")).append(" · ").append(note.optString("date", "")).append("\n\n");
            String question = note.optString("question", "");
            if (!question.isEmpty()) out.append("**Вопрос:** ").append(question).append("\n\n");
            out.append(note.optString("answer", "")).append("\n\n---\n\n");
        }
        return out.toString();
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (resultCode != RESULT_OK || data == null || data.getData() == null) return;
        Uri uri = data.getData();
        if (requestCode == REQ_IMAGE) {
            selectedImage = uri;
            imageName = queryDisplayName(uri);
            renderCurrentTab();
        } else if (requestCode == REQ_EXPORT) {
            try (OutputStream out = getContentResolver().openOutputStream(uri, "w")) {
                if (out == null) throw new IOException("file");
                out.write(notesMarkdown().getBytes(StandardCharsets.UTF_8));
                setStatus("Заметки экспортированы в выбранный файл.", ACCENT);
                Toast.makeText(this, "Экспорт завершён", Toast.LENGTH_SHORT).show();
            } catch (Exception e) {
                setStatus("Не удалось сохранить файл. Выберите другое место.", DANGER);
            }
        }
    }

    private String queryDisplayName(Uri uri) {
        try (android.database.Cursor cursor = getContentResolver().query(uri, null, null, null, null)) {
            if (cursor != null && cursor.moveToFirst()) {
                int index = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME);
                if (index >= 0) return cursor.getString(index);
            }
        } catch (Exception ignored) { }
        return "изображение";
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == REQ_MIC) {
            if (grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED) startRecording();
            else setStatus("Разрешение на микрофон не выдано. Без него аудиозапись недоступна.", DANGER);
        }
    }

    private void setStatus(String message, int color) {
        statusText = message;
        if (globalStatus != null) {
            globalStatus.setText(message);
            globalStatus.setTextColor(color);
        }
        if (lessonStatusView != null) lessonStatusView.setText(message);
    }

    private void updateGlobalStatus() {
        if (globalStatus != null) globalStatus.setText(statusText);
    }

    private interface ValueChanged { void changed(String value); }

    private TextWatcher watcher(ValueChanged callback) {
        return new TextWatcher() {
            @Override public void beforeTextChanged(CharSequence s, int start, int count, int after) { }
            @Override public void onTextChanged(CharSequence s, int start, int before, int count) { callback.changed(s.toString()); }
            @Override public void afterTextChanged(Editable s) { }
        };
    }

    private ScrollView scrollView() {
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setClipToPadding(false);
        scroll.setPadding(0, dp(4), 0, dp(20));
        return scroll;
    }

    private LinearLayout column() {
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        return layout;
    }

    private LinearLayout row() {
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.HORIZONTAL);
        layout.setGravity(Gravity.CENTER_VERTICAL);
        return layout;
    }

    private LinearLayout card() {
        LinearLayout layout = column();
        layout.setPadding(dp(15), dp(14), dp(15), dp(14));
        layout.setBackground(rounded(SURFACE, 20));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, -2);
        lp.bottomMargin = dp(10);
        layout.setLayoutParams(lp);
        return layout;
    }

    private TextView label(String value, int size, int color) {
        TextView view = text(value, size, color, true);
        view.setLetterSpacing(0.04f);
        return view;
    }

    private TextView text(String value, int size, int color, boolean bold) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(TypedValue.COMPLEX_UNIT_SP, size);
        view.setTextColor(color);
        if (bold) view.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        view.setGravity(Gravity.CENTER_VERTICAL);
        return view;
    }

    private EditText editField(String hint, String value, boolean password) {
        EditText edit = new EditText(this);
        edit.setSingleLine(!hint.startsWith("Тема"));
        edit.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        edit.setTextColor(TEXT);
        edit.setHintTextColor(MUTED);
        edit.setHint(hint);
        edit.setPadding(dp(12), dp(8), dp(12), dp(8));
        edit.setBackground(rounded(SURFACE_ALT, 14));
        if (password) edit.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        else edit.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE | InputType.TYPE_TEXT_FLAG_CAP_SENTENCES);
        edit.setText(value);
        return edit;
    }

    private Button button(String value, boolean primary) {
        Button button = new Button(this);
        button.setText(value);
        button.setAllCaps(false);
        button.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
        button.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        button.setTextColor(primary ? BG : TEXT);
        button.setMinHeight(dp(42));
        button.setPadding(dp(8), dp(2), dp(8), dp(2));
        button.setBackground(rounded(primary ? ACCENT : SURFACE_ALT, 15));
        return button;
    }

    private ArrayAdapter<String> spinnerAdapter(List<String> values) {
        return new ArrayAdapter<String>(this, android.R.layout.simple_spinner_item, values) {
            @Override public View getView(int position, View convertView, ViewGroup parent) {
                TextView view = (TextView) super.getView(position, convertView, parent);
                view.setTextColor(TEXT);
                view.setTextSize(TypedValue.COMPLEX_UNIT_SP, 15);
                view.setPadding(dp(8), dp(5), dp(8), dp(5));
                return view;
            }
            @Override public View getDropDownView(int position, View convertView, ViewGroup parent) {
                TextView view = (TextView) super.getDropDownView(position, convertView, parent);
                view.setTextColor(TEXT);
                view.setTextSize(TypedValue.COMPLEX_UNIT_SP, 15);
                view.setPadding(dp(14), dp(12), dp(14), dp(12));
                view.setBackgroundColor(SURFACE_ALT);
                return view;
            }
        } {{ setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item); }};
    }

    private LinearLayout.LayoutParams weightedLp(float weight, int height, int rightMargin) {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, height, weight);
        lp.rightMargin = rightMargin;
        return lp;
    }

    private GradientDrawable rounded(int color, int radius) {
        GradientDrawable drawable = new GradientDrawable();
        drawable.setColor(color);
        drawable.setCornerRadius(dp(radius));
        return drawable;
    }

    private int dp(float value) {
        return (int) (value * getResources().getDisplayMetrics().density + 0.5f);
    }

    @Override
    protected void onDestroy() {
        destroyed = true;
        if (recordingTicker != null) mainHandler.removeCallbacks(recordingTicker);
        if (recording) {
            recording = false;
            try { if (recorder != null) recorder.stop(); } catch (RuntimeException ignored) { }
        }
        releaseRecorder();
        deleteAudio();
        if (activeTask != null) activeTask.cancel(true);
        HttpURLConnection connection = activeConnection;
        if (connection != null) connection.disconnect();
        networkExecutor.shutdownNow();
        super.onDestroy();
    }
}
