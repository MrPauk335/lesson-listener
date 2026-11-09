package org.lessonlistener.app;

import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertSame;
import static org.junit.Assert.assertTrue;

public final class RequestGateTest {
    @Test
    public void staleCompletionCannotMutateUiAfterCancelAndRetry() {
        RequestGate gate = new RequestGate();
        RequestGate.Request canceled = gate.begin();
        RequestGate.Resources canceledResources = gate.cancel();
        assertNull(canceledResources.connection());
        assertNull(canceledResources.audioFile());
        RequestGate.Request retry = gate.begin();
        String[] visibleAnswer = {""};

        assertFalse(gate.complete(canceled, () -> visibleAnswer[0] = "stale answer"));
        assertEquals("", visibleAnswer[0]);
        assertTrue(gate.complete(retry, () -> visibleAnswer[0] = "current answer"));
        assertEquals("current answer", visibleAnswer[0]);
    }

    @Test
    public void staleRequestCannotClearNewRequestsSharedHandles() {
        RequestGate gate = new RequestGate();
        RequestGate.Request oldRequest = gate.begin();
        Object oldConnection = new Object();
        Object oldAudioFile = new Object();
        assertTrue(gate.setConnection(oldRequest, oldConnection));
        assertTrue(gate.setAudioFile(oldRequest, oldAudioFile));

        RequestGate.Resources canceled = gate.cancel();
        assertSame(oldConnection, canceled.connection());
        assertSame(oldAudioFile, canceled.audioFile());

        RequestGate.Request retry = gate.begin();
        Object newConnection = new Object();
        Object newAudioFile = new Object();
        assertTrue(gate.setConnection(retry, newConnection));
        assertTrue(gate.setAudioFile(retry, newAudioFile));

        assertFalse(gate.clearConnection(oldRequest, oldConnection));
        assertFalse(gate.clearAudioFile(oldRequest, oldAudioFile));
        assertFalse(gate.setConnection(oldRequest, new Object()));
        assertFalse(gate.setAudioFile(oldRequest, new Object()));

        RequestGate.Resources stillActive = gate.cancel();
        assertSame(newConnection, stillActive.connection());
        assertSame(newAudioFile, stillActive.audioFile());
    }

    @Test
    public void onlyCurrentRequestCanCompleteOnce() {
        RequestGate gate = new RequestGate();
        RequestGate.Request request = gate.begin();
        int[] updates = {0};

        assertTrue(gate.complete(request, () -> updates[0]++));
        assertFalse(gate.complete(request, () -> updates[0]++));
        assertEquals(1, updates[0]);
    }
}
