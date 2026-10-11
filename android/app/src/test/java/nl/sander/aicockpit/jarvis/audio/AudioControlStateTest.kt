package nl.sander.aicockpit.jarvis.audio

import org.junit.Assert.*
import org.junit.Test

class AudioControlStateTest {
    @Test fun labelsAndButtonsDescribeActualAudio() {
        val cases = mapOf(
            AudioMode.PAUSED to ("Microphone off" to listOf(AudioAction.LISTEN, AudioAction.WAKE)),
            AudioMode.WAITING to ("Waiting for ‘Hey Jarvis’" to listOf(AudioAction.LISTEN)),
            AudioMode.CONNECTING to ("Starting Live…" to listOf(AudioAction.STOP, AudioAction.WAKE)),
            AudioMode.LIVE to ("Live conversation active" to listOf(AudioAction.STOP, AudioAction.WAKE)),
        )
        cases.forEach { (mode, expected) ->
            val state = AudioControlState(mode)
            assertEquals(expected.first, state.text); assertEquals(expected.second, state.actions)
            assertTrue(state.actions.size <= 2)
        }
        assertEquals("Microphone permission needed", AudioControlState(AudioMode.ERROR, error = "Microphone permission needed").text)
    }
    @Test fun repeatedListenDoesNotReplaceTheActiveAttempt() {
        val machine = AudioControlMachine(true)
        assertTrue(machine.listen("a")); val epoch = machine.epoch
        assertFalse(machine.listen("b")); assertEquals(epoch, machine.epoch)
        machine.accepted(epoch, "session", 4); machine.ready(epoch)
        assertFalse(machine.listen("b")); assertEquals("a", machine.state.activationId)
    }
    @Test fun staleStopCannotAffectAReplacement() {
        val machine = AudioControlMachine(false)
        machine.listen("old"); val old = machine.epoch
        machine.accepted(old, "first", 1)
        assertTrue(machine.permitsStop("old", null, null)) // Starting notification remains valid for its activation.
        machine.idle(false); machine.listen("new"); val current = machine.epoch
        machine.accepted(current, "next", 2); machine.ready(current)
        assertFalse(machine.permitsStop("old", "first", 1))
        assertFalse(machine.permitsStop("new", "next", 1))
        assertFalse(machine.permitsStop(null, null, null))
        assertTrue(machine.permitsStop("new", "next", 2))
    }
    @Test fun interruptedStartupCannotPublishReadyOrErrorOverIdle() {
        val machine = AudioControlMachine(true)
        machine.listen("a"); val previous = machine.epoch
        machine.idle(false); machine.stopping(machine.epoch)
        machine.accepted(previous, "late", 1); machine.ready(previous); machine.fail(previous, "late error")
        assertEquals(AudioMode.STOPPING, machine.state.mode)
        machine.paused(machine.epoch); assertEquals(AudioMode.PAUSED, machine.state.mode)
        assertFalse(machine.wakePreferred)
    }
    @Test fun idlePreferenceSurvivesLiveButNeverRestartsItOnRecovery() {
        for (wake in listOf(false, true)) {
            val machine = AudioControlMachine(wake)
            machine.listen("a"); assertEquals(wake, machine.wakePreferred)
            machine.idle(machine.wakePreferred)
            assertEquals(if (wake) AudioMode.WAKE_STARTING else AudioMode.PAUSED, machine.state.mode)
            val recovered = AudioControlMachine(machine.wakePreferred)
            assertFalse(recovered.state.liveActive); assertEquals(wake, recovered.wakePreferred)
        }
    }
}
