package nl.sander.aicockpit.jarvis.wake

data class AddressDecision(val accepted: Boolean, val reason: String)

/** Transcript-stage gate after the local wake model; no audio leaves this policy. */
object JarvisAddressGate {
    private val direct = Regex("^(?:hey\\s+)?jarvis(?:[,.!?\\s]|$)", RegexOption.IGNORE_CASE)
    private val incidental = Regex(
        "\\b(?:about|called|mention(?:ed|ing)?|said)\\s+jarvis\\b|\\bjarvis\\s+(?:movie|character|actor)\\b",
        RegexOption.IGNORE_CASE,
    )

    fun classify(transcript: String, wakeScore: Float, continuedMs: Long): AddressDecision {
        val text = transcript.trim()
        if (incidental.containsMatchIn(text)) return AddressDecision(false, "incidental_mention")
        if (direct.containsMatchIn(text)) return AddressDecision(true, "direct_address")
        return AddressDecision(
            wakeScore >= 0.18f,
            if (wakeScore >= 0.18f) "wake_model_candidate" else "wake_word_not_addressed",
        )
    }
}
