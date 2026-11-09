package org.lessonlistener.app;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.Intent;
import android.os.Bundle;

import java.lang.reflect.Field;
import java.lang.reflect.InvocationHandler;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

public final class RequestRaceInstrumentation extends Instrumentation {
    private static final long TIMEOUT_SECONDS = 5;

    private volatile String stage = "activity launch";
    private CountDownLatch currentRequestGate;

    @Override
    public void onCreate(Bundle arguments) {
        super.onCreate(arguments);
        start();
    }

    @Override
    public void onStart() {
        Activity activity = null;
        int resultCode = 0;
        Bundle result = new Bundle();
        try {
            Intent intent = new Intent(Intent.ACTION_MAIN);
            intent.setClassName(getTargetContext(), MainActivity.class.getName());
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            activity = startActivitySync(intent);

            runSuccessRace(activity);
            runErrorRace(activity);

            resultCode = -1;
            result.putString("stream", "REQUEST_RACE_PASS");
        } catch (Throwable failure) {
            result.putString("stream", "REQUEST_RACE_FAIL: " + stage);
        } finally {
            if (currentRequestGate != null) currentRequestGate.countDown();
            if (activity != null) {
                Activity activityToFinish = activity;
                runOnMainSync(activityToFinish::finish);
            }
        }
        finish(resultCode, result);
    }

    private void runSuccessRace(Activity activity) throws Throwable {
        stage = "success request setup";
        Field answerField = field("answer");
        Field busyField = field("busy");
        Field statusField = field("statusText");
        Field activeTaskField = field("activeTask");
        Method runRequest = requestMethod();
        Method cancelRequest = method("cancelRequest");
        answerField.set(activity, "answer-before-success-race");
        CountDownLatch releaseCurrent = new CountDownLatch(1);
        AtomicReference<Future<?>> currentFuture = new AtomicReference<>();

        runOnMainAndRethrow(() -> {
            invoke(runRequest, activity, "obsolete success", fakeWork(() -> "stale completion"));
            future(activeTaskField, activity).get(TIMEOUT_SECONDS, TimeUnit.SECONDS);
            invoke(cancelRequest, activity);
            invoke(runRequest, activity, "current request active", fakeWork(() -> {
                await(releaseCurrent);
                return "current completion";
            }));
            currentFuture.set(future(activeTaskField, activity));
        });

        waitForIdleSync();
        assertOnMain(() -> {
            require(busyField.getBoolean(activity), "current request was cleared by stale success");
            require("answer-before-success-race".equals(answerField.get(activity)), "stale success changed the answer");
            require("current request active".equals(statusField.get(activity)), "stale success changed the status");
        });

        releaseCurrent.countDown();
        currentFuture.get().get(TIMEOUT_SECONDS, TimeUnit.SECONDS);
        waitForIdleSync();
        assertOnMain(() -> {
            require(!busyField.getBoolean(activity), "current success left the activity busy");
            require("current completion".equals(answerField.get(activity)), "current success answer was not applied");
        });
    }

    private void runErrorRace(Activity activity) throws Throwable {
        stage = "error request setup";
        Field answerField = field("answer");
        Field busyField = field("busy");
        Field statusField = field("statusText");
        Field activeTaskField = field("activeTask");
        Method runRequest = requestMethod();
        Method cancelRequest = method("cancelRequest");
        answerField.set(activity, "answer-before-error-race");
        CountDownLatch releaseCurrent = new CountDownLatch(1);
        AtomicReference<Future<?>> currentFuture = new AtomicReference<>();

        runOnMainAndRethrow(() -> {
            invoke(runRequest, activity, "obsolete error", fakeWork(() -> {
                throw new IllegalStateException("fake failure");
            }));
            future(activeTaskField, activity).get(TIMEOUT_SECONDS, TimeUnit.SECONDS);
            invoke(cancelRequest, activity);
            invoke(runRequest, activity, "current request active", fakeWork(() -> {
                await(releaseCurrent);
                return "current completion";
            }));
            currentFuture.set(future(activeTaskField, activity));
        });

        waitForIdleSync();
        assertOnMain(() -> {
            require(busyField.getBoolean(activity), "current request was cleared by stale error");
            require("answer-before-error-race".equals(answerField.get(activity)), "stale error changed the answer");
            require("current request active".equals(statusField.get(activity)), "stale error changed the status");
        });

        releaseCurrent.countDown();
        currentFuture.get().get(TIMEOUT_SECONDS, TimeUnit.SECONDS);
        waitForIdleSync();
        assertOnMain(() -> {
            require(!busyField.getBoolean(activity), "current request remained busy after error-race retry");
            require("current completion".equals(answerField.get(activity)), "error-race retry answer was not applied");
        });
    }

    private interface Work { String run() throws Exception; }

    private Object fakeWork(Work work) throws ClassNotFoundException {
        Class<?> workType = Class.forName(MainActivity.class.getName() + "$RequestWork");
        InvocationHandler handler = (proxy, method, arguments) -> {
            if ("run".equals(method.getName())) return work.run();
            if ("toString".equals(method.getName())) return "fake request work";
            if ("hashCode".equals(method.getName())) return System.identityHashCode(proxy);
            if ("equals".equals(method.getName())) return proxy == arguments[0];
            throw new UnsupportedOperationException("unexpected fake-work method");
        };
        return Proxy.newProxyInstance(workType.getClassLoader(), new Class<?>[]{workType}, handler);
    }

    private Method requestMethod() throws ClassNotFoundException, NoSuchMethodException {
        Class<?> workType = Class.forName(MainActivity.class.getName() + "$RequestWork");
        return method("runRequest", String.class, workType);
    }

    private Method method(String name, Class<?>... parameters) throws NoSuchMethodException {
        Method method = MainActivity.class.getDeclaredMethod(name, parameters);
        method.setAccessible(true);
        return method;
    }

    private Field field(String name) throws NoSuchFieldException {
        Field field = MainActivity.class.getDeclaredField(name);
        field.setAccessible(true);
        return field;
    }

    private Future<?> future(Field field, Activity activity) throws IllegalAccessException {
        return (Future<?>) field.get(activity);
    }

    private static Object invoke(Method method, Object receiver, Object... arguments) throws Throwable {
        try {
            return method.invoke(receiver, arguments);
        } catch (InvocationTargetException error) {
            throw error.getCause();
        }
    }

    private void runOnMainAndRethrow(Runnable action) throws Throwable {
        AtomicReference<Throwable> failure = new AtomicReference<>();
        runOnMainSync(() -> {
            try {
                action.run();
            } catch (Throwable error) {
                failure.set(error);
            }
        });
        if (failure.get() != null) throw failure.get();
    }

    private void assertOnMain(CheckedAction action) throws Throwable {
        runOnMainAndRethrow(() -> {
            try {
                action.run();
            } catch (Throwable error) {
                throw new AssertionError(error);
            }
        });
    }

    private static void await(CountDownLatch latch) throws Exception {
        if (!latch.await(TIMEOUT_SECONDS, TimeUnit.SECONDS)) throw new AssertionError("fake request timed out");
    }

    private static void require(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }

    private interface CheckedAction { void run() throws Throwable; }
}
