package nl.sander.aicockpit.jarvis.data

import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import okhttp3.sse.EventSources
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test
import java.net.InetAddress
import java.net.ServerSocket
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference
import kotlin.concurrent.thread

class LiveEventStreamClientTest {
    @Test
    fun streamClientOutlastsHeartbeatsAndSharesTheBaseConnectionPool() {
        val base = OkHttpClient()
        val stream = liveEventStreamClient(base)
        assertEquals(TimeUnit.SECONDS.toMillis(LIVE_EVENT_READ_TIMEOUT_SECONDS), stream.readTimeoutMillis.toLong())
        assertTrue(stream.readTimeoutMillis > 2 * 15_000)
        assertSame(base.connectionPool, stream.connectionPool)
        assertSame(base.dispatcher, stream.dispatcher)
    }

    @Test
    fun quietStreamStaysOnOneConnectionPastTheDefaultReadTimeout() {
        val connections = AtomicInteger()
        val server = ServerSocket(0, 1, InetAddress.getLoopbackAddress())
        thread(isDaemon = true) {
            server.accept().use { socket ->
                connections.incrementAndGet()
                val output = socket.getOutputStream()
                output.write(("HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n" +
                    "Cache-Control: no-cache\r\n\r\n").toByteArray())
                output.flush()
                // Longer than OkHttp's default 10 s read timeout, shorter than a heartbeat.
                Thread.sleep(11_000)
                output.write("event: jarvis\ndata: {\"sequence\":1}\n\n".toByteArray())
                output.flush()
                Thread.sleep(500)
            }
        }
        val received = CountDownLatch(1)
        val failure = AtomicReference<Throwable?>()
        val request = Request.Builder().url("http://127.0.0.1:${server.localPort}/api/jarvis/live").build()
        val source = EventSources.createFactory(liveEventStreamClient(OkHttpClient()))
            .newEventSource(request, object : EventSourceListener() {
                override fun onEvent(eventSource: EventSource, id: String?, type: String?, data: String) {
                    received.countDown()
                }
                override fun onFailure(eventSource: EventSource, t: Throwable?, response: Response?) {
                    failure.set(t ?: IllegalStateException("closed: ${response?.code}"))
                    received.countDown()
                }
            })
        try {
            assertTrue(received.await(20, TimeUnit.SECONDS))
            assertNull(failure.get())
            assertEquals(1, connections.get())
        } finally {
            source.cancel()
            server.close()
        }
    }
}
