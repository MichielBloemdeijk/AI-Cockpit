package nl.sander.aicockpit.jarvis.live

import android.content.Context
import android.media.AudioFormat
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.withTimeoutOrNull
import nl.sander.aicockpit.jarvis.data.CockpitApi
import nl.sander.aicockpit.jarvis.data.LiveCall
import nl.sander.aicockpit.jarvis.data.LiveSessionState
import org.webrtc.AudioSource
import org.webrtc.AudioTrack
import org.webrtc.DataChannel
import org.webrtc.IceCandidate
import org.webrtc.MediaConstraints
import org.webrtc.MediaStream
import org.webrtc.PeerConnection
import org.webrtc.PeerConnectionFactory
import org.webrtc.RtpReceiver
import org.webrtc.SdpObserver
import org.webrtc.SessionDescription
import org.webrtc.audio.JavaAudioDeviceModule
import org.json.JSONObject
import kotlin.coroutines.resume
import java.util.UUID
import java.util.concurrent.atomic.AtomicLong

interface LiveCallTransport {
    suspend fun createCall(offer: String, wakeScore: Float?, activationId: String): LiveCall
    suspend fun state(): LiveSessionState?
    suspend fun closeCall(sessionId: String, generation: Int)
}

class ApiLiveCallTransport(private val api: CockpitApi) : LiveCallTransport {
    override suspend fun createCall(offer: String, wakeScore: Float?, activationId: String) = api.createLiveCall(offer, wakeScore, activationId)
    override suspend fun state() = api.liveSessionState()
    override suspend fun closeCall(sessionId: String, generation: Int) = api.closeLive(sessionId, generation)
}

class LiveController(
    private val context: Context,
    private val api: CockpitApi,
    private val transport: LiveCallTransport = ApiLiveCallTransport(api),
) : AutoCloseable {
    private val capturedFrames = AtomicLong()
    private val playedFrames = AtomicLong()
    private val audioModule: JavaAudioDeviceModule = JavaAudioDeviceModule.builder(context)
        .apply {
            setInputSampleRate(LiveActivationAudioBuffer.SAMPLE_RATE)
            setAudioFormat(AudioFormat.ENCODING_PCM_16BIT)
        }
        .setAudioBufferCallback { buffer, audioFormat, channels, sampleRate, byteCount, captureTimeNs ->
            capturedFrames.incrementAndGet()
            AcceptanceAudioInput.replaceWebRtcFrame(buffer, audioFormat, channels, sampleRate, byteCount)
            LiveActivationAudioHandoff.processWebRtcBuffer(
                buffer, audioFormat, channels, sampleRate, byteCount,
            )
            AcceptancePlaybackRecording.input(buffer, byteCount, sampleRate, channels, audioFormat)
            captureTimeNs
        }
        .setPlaybackSamplesReadyCallback { samples ->
            playbackEvidence.lastCallback = android.os.SystemClock.elapsedRealtime()
            if (samples.data.any { it != 0.toByte() }) playedFrames.incrementAndGet()
            AcceptanceAudioInput.observePlayback(samples.data)
            AcceptancePlaybackRecording.append(samples.data, samples.sampleRate, samples.channelCount,
                samples.audioFormat, audioModule)
            AcceptanceAudioStream.output(samples.data, samples.sampleRate, samples.channelCount)
        }
        .createAudioDeviceModule()
    private val factory = PeerConnectionFactory.builder().setAudioDeviceModule(audioModule).createPeerConnectionFactory()
    private var peer: PeerConnection? = null
    private var audioSource: AudioSource? = null
    private var audioTrack: AudioTrack? = null
    private var call: LiveCall? = null
    private var closingCall: LiveCall? = null
    @Volatile private var readyCall: LiveCall? = null
    private var iceReady = CompletableDeferred<Unit>()
    private var eventsChannel: DataChannel? = null
    private var liveActivationId: String? = null
    private var attemptEpoch = 0
    fun currentCall(): LiveCall? = call
    fun audioMetrics() = JSONObject().put("captured_frames", capturedFrames.get()).put("played_frames", playedFrames.get())
    fun observeAcceptanceContinuation(): Boolean {
        val current = call ?: return false
        if (readyCall != current || eventsChannel?.state() != DataChannel.State.OPEN ||
            peer?.connectionState() != PeerConnection.PeerConnectionState.CONNECTED) return false
        AcceptanceAudioInput.observeReady(current.sessionId, current.generation)
        observeAcceptanceTransport()
        return true
    }
    fun observeAcceptanceTransport(checkpoint: Boolean = false) {
        diagnosticScope.launch { AcceptanceAudioInput.observeHealth(context, audioModule, checkpoint) }
        peer?.getStats { report ->
            val audio = report.statsMap.values.filter { it.type == "outbound-rtp" &&
                (it.members["kind"] == "audio" || it.members["mediaType"] == "audio") }
            AcceptanceAudioInput.observeRtp(audio.sumOf { (it.members["bytesSent"] as? Number)?.toLong() ?: 0 },
                audio.sumOf { (it.members["packetsSent"] as? Number)?.toLong() ?: 0 })
        }
    }
    private val diagnosticScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val playbackEvidence by lazy { PlaybackEvidence(context, api, audioModule) { readPlaybackStats() } }

    private suspend fun readPlaybackStats(): org.webrtc.RTCStatsReport? = withTimeoutOrNull(500) {
        kotlinx.coroutines.suspendCancellableCoroutine { continuation ->
            val active = peer
            if (active == null) continuation.resume(null)
            else active.getStats { report -> if (continuation.isActive) continuation.resume(report) }
        }
    }

    suspend fun connect(
        wakeScore: Float? = null,
        activationId: String? = null,
        onState: (String) -> Unit = {},
    ): LiveCall = withContext(Dispatchers.Main) {
        val attempt = ++attemptEpoch
        closePeerOnly()
        iceReady = CompletableDeferred()
        var accepted: LiveCall? = null
        var readiness: LiveStartupReadiness? = null
        var backendObserver: Job? = null
        var latestPeerState = PeerConnection.PeerConnectionState.NEW
        var latestIceState = PeerConnection.IceConnectionState.NEW
        var latestDataState = DataChannel.State.CONNECTING
        var browserSessionStarted = false
        fun ensureCurrent() {
            if (attempt != attemptEpoch) throw CancellationException("GPT-Live startup was cancelled")
        }
        fun record(event: String, detail: JSONObject = JSONObject()) {
            recordDiagnostic(event, attempt, activationId, accepted, detail)
        }
        if (activationId != null && wakeScore != null) {
            LiveActivationAudioHandoff.releaseRecorder(activationId).getOrThrow()
            liveActivationId = activationId
            onState("activation_audio_ready")
        }
        val observer = object : PeerConnection.Observer {
            override fun onSignalingChange(state: PeerConnection.SignalingState) = onState("signaling:${state.name}")
            override fun onConnectionChange(state: PeerConnection.PeerConnectionState) {
                if (attempt != attemptEpoch) return
                latestPeerState = state
                onState("peer:${state.name}")
                record("peer_state", JSONObject().put("state", state.name))
                readiness?.observePeer(state.name)
            }
            override fun onIceConnectionChange(state: PeerConnection.IceConnectionState) {
                if (attempt != attemptEpoch) return
                latestIceState = state
                onState("ice:${state.name}")
                record("ice_state", JSONObject().put("state", state.name))
                readiness?.observeIce(state.name)
            }
            override fun onIceConnectionReceivingChange(receiving: Boolean) = Unit
            override fun onIceGatheringChange(state: PeerConnection.IceGatheringState) {
                onState("gathering:${state.name}")
                if (state == PeerConnection.IceGatheringState.COMPLETE) iceReady.complete(Unit)
            }
            override fun onIceCandidate(candidate: IceCandidate) {
                if (!iceReady.isCompleted) iceReady.complete(Unit)
            }
            override fun onIceCandidatesRemoved(candidates: Array<out IceCandidate>) = Unit
            override fun onAddStream(stream: MediaStream) = Unit
            override fun onRemoveStream(stream: MediaStream) = Unit
            override fun onDataChannel(channel: DataChannel) {
                channel.registerObserver(eventObserver(
                    channel,
                    attempt,
                    onState,
                    true,
                    onChannelState = { state ->
                        latestDataState = state
                        readiness?.observeDataChannel(state.name)
                    },
                    onSessionStarted = {
                        browserSessionStarted = true
                        readiness?.markBrowserSessionStarted()
                    },
                ))
            }
            override fun onRenegotiationNeeded() = Unit
            override fun onAddTrack(receiver: RtpReceiver, streams: Array<out MediaStream>) = Unit
        }
        val configuration = PeerConnection.RTCConfiguration(emptyList()).apply {
            sdpSemantics = PeerConnection.SdpSemantics.UNIFIED_PLAN
            continualGatheringPolicy = PeerConnection.ContinualGatheringPolicy.GATHER_CONTINUALLY
        }
        val connection = factory.createPeerConnection(configuration, observer)
            ?: error("Unable to create WebRTC peer connection")
        peer = connection
        audioSource = factory.createAudioSource(MediaConstraints())
        audioTrack = factory.createAudioTrack("jarvis-microphone", audioSource).also {
            connection.addTrack(it, listOf("jarvis"))
        }
        // Live requires the event transport to exist before the SDP offer is made.
        val events = connection.createDataChannel("oai-events", DataChannel.Init())
        eventsChannel = events
        events.registerObserver(eventObserver(
            events,
            attempt,
            onState,
            true,
            onChannelState = { state ->
                latestDataState = state
                readiness?.observeDataChannel(state.name)
            },
            onSessionStarted = {
                browserSessionStarted = true
                readiness?.markBrowserSessionStarted()
            },
        ))
        val effectiveActivationId = activationId ?: UUID.randomUUID().toString()
        try {
            val offer = connection.createOfferAwait(MediaConstraints().apply {
                mandatory.add(MediaConstraints.KeyValuePair("OfferToReceiveAudio", "true"))
            })
            ensureCurrent()
            connection.setLocalDescriptionAwait(offer)
            ensureCurrent()
            runCatching { withTimeout(750) { iceReady.await() } }
            ensureCurrent()
            val local = connection.localDescription ?: offer
            playbackEvidence.originLower = android.os.SystemClock.elapsedRealtime()
            val established = transport.createCall(local.description, wakeScore, effectiveActivationId)
            if (established.playbackEvidence) playbackEvidence.start(diagnosticScope, established)
            accepted = established
            ensureCurrent()
            // Store the accepted identity before applying the answer or waiting;
            // failure and cancellation must close this exact backend attempt.
            call = established
            onState("accepted")
            readiness = LiveStartupReadiness(established.sessionId, established.generation).also {
                    it.observePeer(latestPeerState.name)
                    it.observeIce(latestIceState.name)
                    it.observeDataChannel(latestDataState.name)
                    if (browserSessionStarted) it.markBrowserSessionStarted()
                }
                backendObserver = launch(Dispatchers.IO) {
                    while (attempt == attemptEpoch && readiness?.current()?.ready != true) {
                        val stateResult = runCatching { transport.state() }
                        if (stateResult.isFailure) {
                            val cause = stateResult.exceptionOrNull()
                            record("backend_readiness_failed", JSONObject().put("message", cause?.message))
                            readiness?.fail(cause?.message ?: "GPT-Live backend readiness check failed")
                            break
                        }
                        val state = stateResult.getOrNull()
                        if (attempt != attemptEpoch) break
                        val wasReady = readiness?.current()?.backendReady == true
                        readiness?.observeBackend(state)
                        val snapshot = readiness?.current()
                        if (!wasReady && snapshot?.backendReady == true) {
                            record("backend_ready")
                        }
                        if (snapshot?.failure != null) break
                        delay(100)
                    }
                }
            connection.setRemoteDescriptionAwait(
                SessionDescription(SessionDescription.Type.ANSWER, established.sdpAnswer)
            )
            ensureCurrent()
                readiness?.markSdpApplied()
                record("sdp_applied")
                try {
                    withTimeout(15_000) {
                        while (true) {
                            ensureCurrent()
                            val snapshot = readiness?.current() ?: error("GPT-Live readiness was not initialized")
                            snapshot.failure?.let { error(it) }
                            if (snapshot.ready) {
                                val finalState = withContext(Dispatchers.IO) { transport.state() }
                                readiness?.observeBackend(finalState)
                                val confirmed = readiness?.current()
                                    ?: error("GPT-Live readiness was not initialized")
                                confirmed.failure?.let { error(it) }
                                if (
                                    finalState?.sessionId == established.sessionId
                                    && finalState.generation == established.generation
                                    && finalState.lifecycleState == "ready"
                                ) break
                            }
                            delay(20)
                        }
                    }
                } catch (_: TimeoutCancellationException) {
                    val missing = readiness?.current()?.missing().orEmpty()
                    error(
                        "GPT-Live startup timed out; missing ${(
                            missing.ifEmpty { listOf("backend sideband health confirmation") }
                        ).joinToString(", ")}",
                    )
                }
                record("backend_health_confirmed")
                if (activationId != null && wakeScore != null) LiveActivationAudioHandoff.claim(activationId).getOrThrow()
                record(
                    "activation_audio_released",
                    JSONObject()
                        .put("buffered", activationId != null)
                        .put("browser_session_started", readiness?.current()?.browserSessionStarted == true),
                )
            ensureCurrent()
            onState("connected")
            readyCall = established
            AcceptanceAudioInput.observeReady(established.sessionId, established.generation)
            established
        } catch (cause: Throwable) {
            val snapshot = readiness?.current()
            record(
                "startup_failed",
                JSONObject()
                    .put("message", cause.message)
                    .put("missing", snapshot?.missing()?.joinToString(", ") ?: "session acceptance"),
            )
            if (attempt == attemptEpoch) attemptEpoch += 1
            stopAudio()
            val exact = accepted
            if (exact != null) withContext(NonCancellable + Dispatchers.IO) {
                withTimeoutOrNull(3_000) { runCatching { transport.closeCall(exact.sessionId, exact.generation) } }
            }
            closingCall = null
            closePeerOnly()
            throw cause
        } finally {
            backendObserver?.cancel()
        }
    }

    private fun eventObserver(
        channel: DataChannel,
        attempt: Int,
        onState: (String) -> Unit,
        diagnosticsEnabled: Boolean,
        onChannelState: (DataChannel.State) -> Unit,
        onSessionStarted: () -> Unit,
    ) = object : DataChannel.Observer {
        override fun onBufferedAmountChange(previousAmount: Long) = Unit
        override fun onStateChange() {
            if (attempt != attemptEpoch) return
            val state = channel.state()
            onState("data:${state.name}")
            if (diagnosticsEnabled) {
                recordDiagnostic(
                    if (state == DataChannel.State.OPEN) "data_channel_open" else "data_channel_state",
                    attempt,
                    liveActivationId,
                    call,
                    JSONObject().put("state", state.name),
                )
            }
            onChannelState(state)
        }
        override fun onMessage(buffer: DataChannel.Buffer) {
            if (attempt != attemptEpoch) return
            val bytes = ByteArray(buffer.data.remaining()); buffer.data.get(bytes)
            val event = String(bytes, Charsets.UTF_8)
            val reduced = reduceLiveEvent(LiveEventState(), event)
            if (reduced.started) {
                playbackEvidence.originUpper = android.os.SystemClock.elapsedRealtime()
                onSessionStarted()
                onState("session.started")
                if (diagnosticsEnabled) {
                    recordDiagnostic("browser_session_started", attempt, liveActivationId, call)
                }
            }
            if (reduced.closed) {
                onState("session.closed")
            }
        }
    }

    /** Release all local audio before any network cleanup or close acknowledgement. */
    fun stopAudio() {
        attemptEpoch += 1
        audioModule.setMicrophoneMute(true)
        audioModule.setSpeakerMute(true)
        if (call != null) closingCall = call
        call = null
        readyCall = null
        closePeerOnly()
    }

    suspend fun disconnect() {
        withContext(Dispatchers.Main) { stopAudio() }
        val active = closingCall
        closingCall = null
        if (active != null) withContext(Dispatchers.IO) {
            withTimeoutOrNull(3_000) { runCatching { transport.closeCall(active.sessionId, active.generation) } }
        }
    }

    override fun close() {
        attemptEpoch += 1
        closePeerOnly()
        diagnosticScope.cancel()
        factory.dispose()
        audioModule.release()
    }

    private fun closePeerOnly() {
        playbackEvidence.stop()
        liveActivationId?.let(LiveActivationAudioHandoff::clear)
        liveActivationId = null
        peer?.close()
        eventsChannel?.close(); eventsChannel?.dispose(); eventsChannel = null
        audioTrack?.dispose(); audioTrack = null
        audioSource?.dispose(); audioSource = null
        peer?.dispose(); peer = null
    }

    private fun recordDiagnostic(
        event: String,
        attempt: Int,
        activationId: String?,
        activeCall: LiveCall?,
        detail: JSONObject = JSONObject(),
    ) {
        val traceId = activationId ?: activeCall?.sessionId ?: "android-live-attempt-$attempt"
        AcceptanceAudioInput.observeDiagnostic(event, JSONObject(detail.toString()).put("attempt", attempt))
        diagnosticScope.launch {
            runCatching {
                api.submitLiveDiagnostic(
                    traceId = traceId,
                    eventType = "live.$event",
                    activationId = activationId,
                    sessionId = activeCall?.sessionId,
                    generation = activeCall?.generation,
                    detail = JSONObject(detail.toString()).put("attempt", attempt),
                )
            }
        }
    }
}

private suspend fun PeerConnection.createOfferAwait(constraints: MediaConstraints): SessionDescription =
    suspendCancellableCoroutineCompat { observer -> createOffer(observer, constraints) }

private suspend fun PeerConnection.setLocalDescriptionAwait(description: SessionDescription) =
    suspendCancellableCoroutineCompat<Unit> { observer -> setLocalDescription(observer, description) }

private suspend fun PeerConnection.setRemoteDescriptionAwait(description: SessionDescription) =
    suspendCancellableCoroutineCompat<Unit> { observer -> setRemoteDescription(observer, description) }

private suspend fun <T> suspendCancellableCoroutineCompat(block: (SdpObserver) -> Unit): T =
    kotlinx.coroutines.suspendCancellableCoroutine { continuation ->
        block(object : SdpObserver {
            override fun onCreateSuccess(description: SessionDescription) {
                @Suppress("UNCHECKED_CAST")
                if (continuation.isActive) continuation.resume(description as T)
            }
            override fun onSetSuccess() {
                @Suppress("UNCHECKED_CAST")
                if (continuation.isActive) continuation.resume(Unit as T)
            }
            override fun onCreateFailure(error: String) {
                if (continuation.isActive) continuation.resumeWith(Result.failure(IllegalStateException(error)))
            }
            override fun onSetFailure(error: String) {
                if (continuation.isActive) continuation.resumeWith(Result.failure(IllegalStateException(error)))
            }
        })
    }
