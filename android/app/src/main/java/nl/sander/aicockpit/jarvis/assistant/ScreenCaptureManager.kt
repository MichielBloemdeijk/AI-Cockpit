package nl.sander.aicockpit.jarvis.assistant

import android.content.Context
import android.graphics.Bitmap
import android.os.SystemClock
import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import nl.sander.aicockpit.jarvis.data.CockpitApi
import nl.sander.aicockpit.jarvis.data.CredentialStore
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.time.Instant
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import nl.sander.aicockpit.jarvis.live.AcceptanceAudioInput

/** Process-memory-only screenshot handoff. No pixel reaches the network before a run-bound request. */
class ScreenCaptureManager(private val context: Context) {
    private val store = CredentialStore(context)
    private val api = CockpitApi(store)
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val attemptedRequests = ConcurrentHashMap.newKeySet<String>()
    private val lock = Any()
    private var active: Capture? = null
    private val retained = mutableMapOf<String, Capture>()
    private val keyguard = context.getSystemService(android.app.KeyguardManager::class.java)
    @Volatile private var contextDeliveredNs: Long? = null

    data class Capture(
        val activationId: String,
        val captureId: String,
        val revision: Int,
        val status: String,
        val expiresAt: Instant,
        val capturedAt: Instant? = null,
        val callbackMonotonicMs: Double? = null,
        val jpeg: ByteArray? = null,
        val reason: String? = null,
        val sessionId: String? = null,
        val generation: Int? = null,
        val captureMode: String = "activation",
    ) {
        fun usableFor(request: JSONObject, now: Instant): Boolean =
            status == "captured" && jpeg != null && now.isBefore(expiresAt) &&
                activationId == request.optString("activation_id") &&
                captureId == request.optString("capture_id") &&
                generation == request.optInt("generation")
    }

    fun startActivation(activationId: String, captureId: String = UUID.randomUUID().toString()) {
        val capture = Capture(activationId, captureId, 1, "pending", Instant.now().plusSeconds(90))
        synchronized(lock) { retained.values.forEach { it.jpeg?.fill(0) }; active = capture; retained.clear(); retained[captureId] = capture }
        expire(capture)
        AcceptanceAudioInput.observeCapture("requested", captureId, JSONObject().put("mode", "activation"))
        scope.launch { report(capture) }
    }

    /** Server-created capture, already bound to the paired browser's voice call. */
    internal fun startPhoneCapture(request: PhoneCaptureRequest) {
        bindSession(request.activationId, request.sessionId, request.generation)
        val capture = synchronized(lock) {
            if (retained.containsKey(request.captureId)) return
            if (active?.activationId != request.activationId || active?.sessionId != request.sessionId ||
                active?.generation != request.generation) {
                retained.values.forEach { it.jpeg?.fill(0) }
                retained.clear()
            }
            Capture(request.activationId, request.captureId, 2, "pending", request.expiresAt,
                sessionId = request.sessionId, generation = request.generation,
                captureMode = request.mode).also { active = it; retained[it.captureId] = it }
        }
        expire(capture)
        scope.launch { report(capture) }
    }

    fun startFreshCapture(activationId: String, captureId: String, expiresAt: Instant? = null): Boolean {
        val capture = synchronized(lock) {
            val previous = active?.takeIf { it.activationId == activationId } ?: return false
            if (retained.containsKey(captureId)) return true
            Capture(activationId, captureId, 1, "pending", expiresAt ?: Instant.now().plusSeconds(90),
                sessionId = previous.sessionId, generation = previous.generation,
                captureMode = "fresh").also { active = it; retained[captureId] = it }
        }
        expire(capture)
        AcceptanceAudioInput.observeCapture("requested", captureId, JSONObject().put("mode", "fresh"))
        scope.launch { report(capture) }
        return true
    }

    fun unavailable(reason: String, captureId: String? = currentCaptureId()) {
        if (reason == "locked") {
            val invalidated = synchronized(lock) {
                val images = retained.values.map { image ->
                    image.jpeg?.fill(0)
                    image.copy(jpeg = null, status = "unavailable", reason = "locked", revision = image.revision + 1)
                }
                retained.clear()
                active = null
                images
            }
            invalidated.forEach { image -> scope.launch { report(image) } }
            return
        }
        val capture = synchronized(lock) { retained[captureId] } ?: return
        scope.launch { transition(capture, "unavailable", Instant.now(),
            SystemClock.elapsedRealtime().toDouble(), null, reason) }
    }

    fun currentCaptureId(): String? = synchronized(lock) { active?.captureId }
    fun clear() { synchronized(lock) { retained.values.forEach { it.jpeg?.fill(0) }; retained.clear(); active = null } }
    fun matchesActivation(activationId: String): Boolean = synchronized(lock) { active?.activationId == activationId }

    private fun expire(capture: Capture) {
        scope.launch {
            delay(java.time.Duration.between(Instant.now(), capture.expiresAt).toMillis().coerceAtLeast(0))
            synchronized(lock) {
                retained.remove(capture.captureId)?.jpeg?.fill(0)
                if (active?.captureId == capture.captureId) active = null
            }
        }
    }

    fun onScreenshot(bitmap: Bitmap?, locked: Boolean, captureId: String? = currentCaptureId()) {
        val synchronousStarted = SystemClock.elapsedRealtimeNanos()
        if (locked || PhoneScreenPrivacy.locked(context)) { unavailable("locked"); return }
        val original = synchronized(lock) { retained[captureId] } ?: return
        val callbackAt = Instant.now()
        val callbackMs = SystemClock.elapsedRealtime().toDouble()
        scope.launch {
            if (locked || bitmap == null) {
                transition(original, "unavailable", callbackAt, callbackMs, null,
                    if (locked) "locked" else "callback_null")
                return@launch
            }
            // Android 17 can redact FLAG_SECURE to a non-null black bitmap.
            // Uniform black content is not evidence of a readable screen. This
            // deliberately also rejects a genuinely all-black app screen; do
            // not claim we can infer which platform policy caused it.
            val black = runCatching { withContext(Dispatchers.Default) { isBlankBlackContent(bitmap) } }.getOrDefault(true)
            if (black) {
                transition(original, "unavailable", callbackAt, callbackMs, null, "platform_or_app_policy")
                return@launch
            }
            val jpeg = runCatching { withContext(Dispatchers.Default) { encodeJpeg(bitmap) } }.getOrNull()
            AcceptanceAudioInput.observeCapture("encoding_finished", original.captureId, JSONObject()
                .put("mode", original.captureMode).put("callback_ns", synchronousStarted)
                .put("encoded", jpeg != null))
            if (jpeg == null) {
                transition(original, "unavailable", callbackAt, callbackMs, null, "encoding_failed")
            } else {
                transition(original, "captured", callbackAt, callbackMs, jpeg, null)
            }
        }
        AcceptanceAudioInput.observeCapture("callback", original.captureId, JSONObject()
            .put("mode", original.captureMode).put("callback_ns", synchronousStarted)
            .put("synchronous_duration_ns", SystemClock.elapsedRealtimeNanos() - synchronousStarted)
            .put("bitmap_present", bitmap != null).put("locked", locked))
    }

    fun bindSession(activationId: String, sessionId: String, generation: Int) {
        val bound = synchronized(lock) {
            val old = active?.takeIf { it.activationId == activationId } ?: return
            if (old.sessionId == sessionId && old.generation == generation) return
            old.copy(revision = old.revision + 1, sessionId = sessionId, generation = generation).also { active = it; retained[it.captureId] = it }
        }
        scope.launch { report(bound) }
    }

    fun onContextRequested() {
        contextDeliveredNs = SystemClock.elapsedRealtimeNanos()
        scope.launch { flushPending() }
    }
    fun onStreamConnected() { scope.launch { flushPending() } }

    private suspend fun flushPending() {
        val requests = runCatching { api.pendingContextRequests() }.getOrElse { return }
        coroutineScope { requests.map { request -> async {
            val requestId = request.optString("request_id")
            if (requestId.isBlank() || !attemptedRequests.add(requestId)) return@async
            val capture = synchronized(lock) {
                retained.values.firstOrNull { it.usableFor(request, Instant.now()) }
                    ?.let { it.copy(jpeg = it.jpeg?.copyOf()) }
            }
            if (PhoneScreenPrivacy.locked(context) || capture == null || !capture.usableFor(request, Instant.now())) {
                capture?.jpeg?.fill(0)
                if (PhoneScreenPrivacy.locked(context)) unavailable("locked")
                runCatching { api.failContext(requestId, if (PhoneScreenPrivacy.locked(context)) "locked" else "activation_replaced") }
                return@async
            }
            val jpeg = capture.jpeg ?: return@async
            val capturedAt = capture.capturedAt ?: return@async
            val callbackMs = capture.callbackMonotonicMs ?: return@async
            AcceptanceAudioInput.observeCapture("upload_started", capture.captureId, JSONObject()
                .put("request_id", requestId).put("context_delivered_ns", contextDeliveredNs)
                .put("upload_started_ns", SystemClock.elapsedRealtimeNanos()))
            val uploadStarted = SystemClock.elapsedRealtimeNanos()
            val upload = runCatching { api.uploadContext(request, jpeg, capturedAt, callbackMs) }
                .onFailure { runCatching { api.failContext(requestId, "upload_failed") } }
            AcceptanceAudioInput.observeCapture("upload_finished", capture.captureId, JSONObject()
                .put("request_id", requestId).put("succeeded", upload.isSuccess)
                .put("duration_ms", (SystemClock.elapsedRealtimeNanos() - uploadStarted) / 1_000_000.0))
            jpeg.fill(0)
        } }.awaitAll() }
    }

    private suspend fun transition(
        original: Capture, status: String, callbackAt: Instant, callbackMs: Double,
        jpeg: ByteArray?, reason: String?,
    ) {
        val next = synchronized(lock) {
            val current = retained[original.captureId]?.takeIf { it.status == "pending" }
                ?: run {
                    Log.w("JarvisScreenCapture", "Ignoring stale screenshot callback")
                    jpeg?.fill(0)
                    return
                }
            current.copy(
                revision = current.revision + 1, status = status,
                capturedAt = if (status == "captured") callbackAt else null,
                callbackMonotonicMs = callbackMs, jpeg = jpeg, reason = reason,
                expiresAt = current.expiresAt,
            ).also {
                retained[it.captureId] = it
                if (active?.captureId == it.captureId) active = it
            }
        }
        report(next)
    }

    private suspend fun report(capture: Capture) {
        if (store.credential().isNullOrBlank()) return
        val payload = JSONObject()
            .put("activation_id", capture.activationId)
            .put("capture_mode", capture.captureMode)
            .put("status", capture.status)
            .put("revision", capture.revision)
            .put("expires_at", capture.expiresAt.toString())
        capture.capturedAt?.let { payload.put("captured_at", it.toString()) }
        capture.callbackMonotonicMs?.let { payload.put("callback_monotonic_ms", it) }
        capture.reason?.let { payload.put("reason", it) }
        capture.sessionId?.let { payload.put("session_id", it) }
        capture.generation?.let { payload.put("generation", it) }
        val started = SystemClock.elapsedRealtimeNanos()
        AcceptanceAudioInput.observeCapture("status_report_started", capture.captureId,
            JSONObject().put("status", capture.status).put("revision", capture.revision))
        val result = runCatching { api.reportCapture(capture.captureId, payload) }
            .onFailure { Log.w("JarvisScreenCapture", "Capture status report failed: ${capture.status}", it) }
        AcceptanceAudioInput.observeCapture("status_report_finished", capture.captureId,
            JSONObject().put("status", capture.status).put("revision", capture.revision)
                .put("succeeded", result.isSuccess)
                .put("duration_ms", (SystemClock.elapsedRealtimeNanos() - started) / 1_000_000.0))
    }

    private fun encodeJpeg(bitmap: Bitmap): ByteArray? {
        if (bitmap.isRecycled || bitmap.width < 1 || bitmap.height < 1) return null
        val scale = minOf(1.0, 2048.0 / maxOf(bitmap.width, bitmap.height))
        val image = if (scale < 1.0) Bitmap.createScaledBitmap(
            bitmap, (bitmap.width * scale).toInt().coerceAtLeast(1),
            (bitmap.height * scale).toInt().coerceAtLeast(1), true,
        ) else bitmap
        try {
            for (quality in listOf(85, 70, 55)) {
                val output = ByteArrayOutputStream()
                if (image.compress(Bitmap.CompressFormat.JPEG, quality, output)) {
                    val bytes = output.toByteArray()
                    if (bytes.size <= 5 * 1024 * 1024) return bytes
                }
            }
            return null
        } finally {
            if (image !== bitmap) image.recycle()
        }
    }

    private fun isBlankBlackContent(bitmap: Bitmap): Boolean {
        if (bitmap.isRecycled || bitmap.width < 1 || bitmap.height < 1) return true
        val row = IntArray(bitmap.width)
        // Protected app pixels may be black while system status/navigation
        // chrome remains visible. A conservative central-80% content gate
        // avoids treating those icons as readable app evidence.
        val margin = bitmap.height / 10
        for (y in margin until bitmap.height - margin) {
            bitmap.getPixels(row, 0, bitmap.width, 0, y, bitmap.width, 1)
            if (row.any { it and 0x00ffffff != 0 }) return false
        }
        return true
    }
}
