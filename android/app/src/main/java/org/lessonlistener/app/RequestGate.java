package org.lessonlistener.app;

final class RequestGate {
    static final class Request {
        private final long generation;

        private Request(long generation) {
            this.generation = generation;
        }
    }

    static final class Resources {
        private final Object connection;
        private final Object audioFile;

        private Resources(Object connection, Object audioFile) {
            this.connection = connection;
            this.audioFile = audioFile;
        }

        Object connection() { return connection; }
        Object audioFile() { return audioFile; }
    }

    private long nextGeneration;
    private long activeGeneration;
    private Object activeConnection;
    private Object activeAudioFile;

    synchronized Request begin() {
        if (activeGeneration != 0) throw new IllegalStateException("A request is already active");
        if (++nextGeneration == 0) ++nextGeneration;
        activeGeneration = nextGeneration;
        return new Request(activeGeneration);
    }

    synchronized boolean isActive(Request request) {
        return request != null && activeGeneration == request.generation;
    }

    synchronized boolean complete(Request request, Runnable completion) {
        if (!isActive(request)) return false;
        activeGeneration = 0;
        activeConnection = null;
        activeAudioFile = null;
        completion.run();
        return true;
    }

    synchronized Resources cancel() {
        Resources resources = new Resources(activeConnection, activeAudioFile);
        activeGeneration = 0;
        activeConnection = null;
        activeAudioFile = null;
        return resources;
    }

    synchronized boolean setConnection(Request request, Object connection) {
        if (!isActive(request)) return false;
        activeConnection = connection;
        return true;
    }

    synchronized boolean clearConnection(Request request, Object connection) {
        if (!isActive(request) || activeConnection != connection) return false;
        activeConnection = null;
        return true;
    }

    synchronized boolean setAudioFile(Request request, Object audioFile) {
        if (!isActive(request)) return false;
        activeAudioFile = audioFile;
        return true;
    }

    synchronized boolean clearAudioFile(Request request, Object audioFile) {
        if (!isActive(request) || activeAudioFile != audioFile) return false;
        activeAudioFile = null;
        return true;
    }
}
