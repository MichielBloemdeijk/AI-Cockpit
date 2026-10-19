package nl.sander.aicockpit.jarvis.live

import android.content.Context
import android.content.pm.ApplicationInfo
import android.util.Base64
import android.util.Log
import org.json.JSONObject
import java.io.DataInputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.TimeUnit

/**
 * Debug-only live audio relay for agent self-tests with a simulated user.
 *
 * A loopback socket (reached through `adb forward`) carries the simulated user's
 * 16 kHz mono PCM into the microphone seam, and the app's playback PCM back out as
 * JSON lines. It only exists after an explicit debug broadcast; release builds
 * never start it because the app must be debuggable.
 */
object AcceptanceAudioStream {
    private const val RATE = 16_000
    private const val MAX_BUFFERED = RATE * 4  // four seconds of caller audio
    private val ring = ShortArray(MAX_BUFFERED)
    private var readAt = 0
    private var available = 0
    private var server: ServerSocket? = null
    private var client: Socket? = null
    private var outgoing: ArrayBlockingQueue<String>? = null
    @Volatile var active = false
        private set

    @Synchronized
    fun start(context: Context, port: Int) {
        check(context.applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0)
        require(port in 1024..65535)
        stop()
        val socket = ServerSocket(port, 1, InetAddress.getLoopbackAddress())
        server = socket
        outgoing = ArrayBlockingQueue(4096)
        active = true
        Thread({
            runCatching {
                val connection = socket.accept().also { synchronized(this) { client = it } }
                Thread({ writeLoop(connection) }, "jarvis-acceptance-stream-out").apply { isDaemon = true; start() }
                val input = DataInputStream(connection.getInputStream().buffered())
                val bytes = ByteArray(640)
                while (active) {
                    val count = input.read(bytes)
                    if (count < 0) break
                    push(bytes, count)
                }
            }.onFailure { if (active) Log.w("JarvisAudioAcceptance", "Audio stream ended", it) }
            stop()
        }, "jarvis-acceptance-stream-in").apply { isDaemon = true; start() }
        Log.i("JarvisAudioAcceptance", JSONObject().put("event", "stream_listening").put("port", port).toString())
    }

    private fun writeLoop(connection: Socket) {
        runCatching {
            val writer = connection.getOutputStream().bufferedWriter()
            while (active) {
                val line = outgoing?.poll(100, TimeUnit.MILLISECONDS) ?: continue
                writer.write(line); writer.newLine(); writer.flush()
            }
        }
    }

    @Synchronized
    private fun push(bytes: ByteArray, count: Int) {
        var index = 0
        while (index + 1 < count) {
            val sample = ((bytes[index + 1].toInt() shl 8) or (bytes[index].toInt() and 0xff)).toShort()
            if (available == MAX_BUFFERED) {  // drop the oldest sample rather than drift
                readAt = (readAt + 1) % MAX_BUFFERED; available--
            }
            ring[(readAt + available) % MAX_BUFFERED] = sample
            available++
            index += 2
        }
    }

    /** Replace one microphone frame with relayed caller audio (silence when none is buffered). */
    @Synchronized
    fun fill(frame: ShortArray, count: Int) {
        repeat(count) {
            if (available > 0) {
                frame[it] = ring[readAt]; readAt = (readAt + 1) % MAX_BUFFERED; available--
            } else frame[it] = 0
        }
    }

    /** Forward the app's playback PCM to the host. Never blocks the audio callback. */
    fun output(bytes: ByteArray, rate: Int, channels: Int) {
        val queue = outgoing ?: return
        if (!active) return
        queue.offer(JSONObject().put("rate", rate).put("channels", channels)
            .put("pcm", Base64.encodeToString(bytes, Base64.NO_WRAP)).toString())
    }

    @Synchronized
    fun stop() {
        active = false
        runCatching { client?.close() }
        runCatching { server?.close() }
        client = null; server = null; outgoing = null
        readAt = 0; available = 0; ring.fill(0)
    }
}
