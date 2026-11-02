package nl.sander.aicockpit.jarvis.live

/** Measured frame completeness; no frames or lost observations cannot prove smoothness. */
class AcceptanceFrameMetrics {
    var count = 0L; private set
    var janky = 0L; private set
    var dropped = 0L; private set
    var invalid = 0L; private set
    var firstDraws = 0L; private set
    var firstDrawOverruns = 0L; private set

    fun add(durationNs: Long, deadlineNs: Long, droppedSinceLast: Int, firstDrawFlag: Long = 0) {
        if (droppedSinceLast < 0) invalid++ else dropped += droppedSinceLast
        if (durationNs < 0 || deadlineNs <= 0 || firstDrawFlag !in 0L..1L) { invalid++; return }
        count++
        if (firstDrawFlag == 1L) {
            firstDraws++
            if (durationNs >= deadlineNs) firstDrawOverruns++
            return
        }
        if (durationNs >= deadlineNs) janky++
    }

    fun reset() { count = 0; janky = 0; dropped = 0; invalid = 0; firstDraws = 0; firstDrawOverruns = 0 }
}
