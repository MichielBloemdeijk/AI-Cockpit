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
        if (!direct.containsMatchIn(text)) return AddressDecision(false, "wake_word_not_addressed")
        val timing = if (continuedMs >= 180L) 0.18f else -0.12f
        val confidence = (wakeScore * 0.62f + 0.28f + timing).coerceIn(0f, 1f)
        return AddressDecision(confidence >= 0.72f, if (confidence >= 0.72f) "direct_address" else "low_confidence")
    }
}
