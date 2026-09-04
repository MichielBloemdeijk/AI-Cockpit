package nl.sander.aicockpit.jarvis.wake

/**
 * Recall-first wake decision. Speech and wake-model peaks do not need to land
 * in the same 120 ms inference window.
 */
class WakeCandidateGate(
    private val strongWakeThreshold: Float = 0.45f,
    private val candidateWakeThreshold: Float = 0.18f,
    private val candidateHitsRequired: Int = 2,
    private val positiveVadThreshold: Float = 0.65f,
    private val speechLookbackWindows: Int = 10,
    private val candidateLookbackWindows: Int = 8,
) {
    private var recentSpeechWindows = 0
    private var candidateWindows = 0
    private var candidateHits = 0
    private var candidateProbability = 0f

    fun evaluate(result: WakeInference): Float? {
        if (result.speechProbability >= positiveVadThreshold) {
            recentSpeechWindows = speechLookbackWindows
        } else if (recentSpeechWindows > 0) {
            recentSpeechWindows -= 1
        }

        if (candidateWindows > 0) {
            candidateWindows -= 1
        } else {
            candidateHits = 0
            candidateProbability = 0f
        }
        if (result.wakeProbability >= candidateWakeThreshold) {
            candidateHits += 1
            candidateProbability = maxOf(candidateProbability, result.wakeProbability)
            candidateWindows = candidateLookbackWindows
        }

        val strongWake = result.wakeProbability >= strongWakeThreshold
        val repeatedCandidate = candidateHits >= candidateHitsRequired
        if (recentSpeechWindows <= 0 || (!strongWake && !repeatedCandidate)) return null
        return maxOf(result.wakeProbability, candidateProbability).also { reset() }
    }

    fun reset() {
        recentSpeechWindows = 0
        candidateWindows = 0
        candidateHits = 0
        candidateProbability = 0f
    }
}
