package nl.sander.aicockpit.jarvis.realtime

import android.content.Context
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.withTimeoutOrNull
import nl.sander.aicockpit.jarvis.data.CockpitApi
import nl.sander.aicockpit.jarvis.data.RealtimeCall
import nl.sander.aicockpit.jarvis.data.VoiceEngine
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
import kotlin.coroutines.resume
import java.util.UUID

class RealtimeController(context: Context, private val api: CockpitApi) : AutoCloseable {
    private val audioModule = JavaAudioDeviceModule.builder(context).createAudioDeviceModule()
    private val factory = PeerConnectionFactory.builder().setAudioDeviceModule(audioModule).createPeerConnectionFactory()
    private var peer: PeerConnection? = null
    private var audioSource: AudioSource? = null
    private var audioTrack: AudioTrack? = null
    private var call: RealtimeCall? = null
    private var iceReady = CompletableDeferred<Unit>()
    private var liveStarted = CompletableDeferred<Unit>()
    private var liveClosed = CompletableDeferred<Unit>()
    private var eventsChannel: DataChannel? = null
    private var activeEngine: VoiceEngine = VoiceEngine.REALTIME

    suspend fun connect(wakeScore: Float? = null, engine: VoiceEngine = VoiceEngine.REALTIME, onState: (String) -> Unit = {}): RealtimeCall = withContext(Dispatchers.Main) {
        closePeerOnly()
        iceReady = CompletableDeferred()
        liveStarted = CompletableDeferred()
        liveClosed = CompletableDeferred()
        activeEngine = engine
        val observer = object : PeerConnection.Observer {
            override fun onSignalingChange(state: PeerConnection.SignalingState) = onState("signaling:${state.name}")
            override fun onIceConnectionChange(state: PeerConnection.IceConnectionState) = onState("ice:${state.name}")
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
            override fun onDataChannel(channel: DataChannel) { channel.registerObserver(eventObserver(channel, onState)) }
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
        events.registerObserver(eventObserver(events, onState))
        val activationId = UUID.randomUUID().toString()
        val offer = connection.createOfferAwait(MediaConstraints().apply {
            mandatory.add(MediaConstraints.KeyValuePair("OfferToReceiveAudio", "true"))
        })
        connection.setLocalDescriptionAwait(offer)
        runCatching { withTimeout(750) { iceReady.await() } }
        val local = connection.localDescription ?: offer
        val established = api.createRealtimeCall(local.description, wakeScore, engine, activationId)
        connection.setRemoteDescriptionAwait(
            SessionDescription(SessionDescription.Type.ANSWER, established.sdpAnswer)
        )
        if (engine == VoiceEngine.GPT_LIVE) {
            withTimeout(15_000) { liveStarted.await() }
        }
        call = established
        onState("connected")
        established
    }

    private fun eventObserver(channel: DataChannel, onState: (String) -> Unit) = object : DataChannel.Observer {
        override fun onBufferedAmountChange(previousAmount: Long) = Unit
        override fun onStateChange() = Unit
        override fun onMessage(buffer: DataChannel.Buffer) {
            val bytes = ByteArray(buffer.data.remaining()); buffer.data.get(bytes)
            val event = String(bytes, Charsets.UTF_8)
            val reduced = reduceLiveEvent(LiveEventState(), event)
            if (reduced.started) {
                liveStarted.complete(Unit)
                onState("session.started")
            }
            if (reduced.closed) {
                liveClosed.complete(Unit)
                onState("session.closed")
            }
        }
    }

    suspend fun disconnect() {
        val active = call
        call = null
        if (activeEngine == VoiceEngine.GPT_LIVE && eventsChannel?.state() == DataChannel.State.OPEN) {
            val closeJson = "{\"type\":\"session.close\"}".toByteArray(Charsets.UTF_8)
            eventsChannel?.send(DataChannel.Buffer(java.nio.ByteBuffer.wrap(closeJson), false))
            withTimeoutOrNull(2_000) { liveClosed.await() }
        }
        if (active != null) runCatching { api.closeRealtime(active.sessionId, active.generation) }
        withContext(Dispatchers.Main) { closePeerOnly() }
    }

    override fun close() {
        closePeerOnly()
        factory.dispose()
        audioModule.release()
    }

    private fun closePeerOnly() {
        eventsChannel?.close(); eventsChannel?.dispose(); eventsChannel = null
        audioTrack?.dispose(); audioTrack = null
        audioSource?.dispose(); audioSource = null
        peer?.close(); peer?.dispose(); peer = null
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
