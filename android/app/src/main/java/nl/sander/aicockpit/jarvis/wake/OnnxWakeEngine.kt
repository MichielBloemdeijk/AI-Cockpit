package nl.sander.aicockpit.jarvis.wake

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import android.content.Context
import java.nio.FloatBuffer
import java.nio.LongBuffer

data class WakeInference(val speechProbability: Float, val wakeProbability: Float)

/** Android port of the same four-model Hey Buddy pipeline used by the PWA. */
class OnnxWakeEngine(context: Context) : AutoCloseable {
    private val environment = OrtEnvironment.getEnvironment()
    private val vad = session(context, "models/silero-vad.onnx")
    private val spectrogram = session(context, "models/mel-spectrogram.onnx")
    private val embedding = session(context, "models/speech-embedding.onnx")
    private val wake = session(context, "models/jarvis.onnx")
    private var stateH = FloatArray(128)
    private var stateC = FloatArray(128)
    private var history = FloatArray(0)

    fun analyze(audio: FloatArray): WakeInference {
        val vadInput = audio.copyOfRange((audio.size - VAD_INTERVAL_SAMPLES).coerceAtLeast(0), audio.size)
        val speechProbability = OnnxTensor.createTensor(environment, FloatBuffer.wrap(vadInput), longArrayOf(1, vadInput.size.toLong())).use { input ->
            OnnxTensor.createTensor(environment, FloatBuffer.wrap(stateH), longArrayOf(2, 1, 64)).use { h ->
                OnnxTensor.createTensor(environment, FloatBuffer.wrap(stateC), longArrayOf(2, 1, 64)).use { c ->
                    OnnxTensor.createTensor(environment, LongBuffer.wrap(longArrayOf(16_000)), longArrayOf(1)).use { sr ->
                        vad.run(mapOf("input" to input, "h" to h, "c" to c, "sr" to sr)).use { output ->
                            stateH = (output.get("hn").orElseThrow() as OnnxTensor).floatBuffer.toFloatArray()
                            stateC = (output.get("cn").orElseThrow() as OnnxTensor).floatBuffer.toFloatArray()
                            (output.get("output").orElseThrow() as OnnxTensor).floatBuffer.get(0)
                        }
                    }
                }
            }
        }
        var mel = FloatArray(0)
        var frames = 0
        var bins = 0
        OnnxTensor.createTensor(environment, FloatBuffer.wrap(audio), longArrayOf(1, audio.size.toLong())).use { input ->
            spectrogram.run(mapOf("input" to input)).use { output ->
                val tensor = output.get("output").orElseThrow() as OnnxTensor
                val shape = tensor.info.shape
                frames = shape[shape.size - 2].toInt()
                bins = shape.last().toInt()
                mel = tensor.floatBuffer.toFloatArray().also { values ->
                    for (index in values.indices) values[index] = values[index] / 10f + 2f
                }
            }
        }
        appendHistory(runEmbedding(mel, frames, bins))
        val wakeProbability = if (history.size == WAKE_FRAMES * EMBEDDING_DIMENSION) {
            OnnxTensor.createTensor(
                environment, FloatBuffer.wrap(history), longArrayOf(1, WAKE_FRAMES.toLong(), EMBEDDING_DIMENSION.toLong())
            ).use { input ->
                wake.run(mapOf("input" to input)).use { output ->
                    (output.get("output").orElseThrow() as OnnxTensor).floatBuffer.get(0)
                }
            }
        } else 0f
        vadInput.fill(0f)
        return WakeInference(speechProbability, wakeProbability)
    }

    private fun runEmbedding(mel: FloatArray, frames: Int, bins: Int): FloatArray {
        require(frames >= EMBEDDING_WINDOW) { "Wake spectrogram has too few frames" }
        val truncated = frames - ((frames - EMBEDDING_WINDOW) % EMBEDDING_STRIDE)
        val batches = (truncated - EMBEDDING_WINDOW) / EMBEDDING_STRIDE + 1
        val stacked = FloatArray(batches * EMBEDDING_WINDOW * bins)
        repeat(batches) { batch ->
            mel.copyInto(
                stacked,
                batch * EMBEDDING_WINDOW * bins,
                batch * EMBEDDING_STRIDE * bins,
                batch * EMBEDDING_STRIDE * bins + EMBEDDING_WINDOW * bins,
            )
        }
        return OnnxTensor.createTensor(
            environment,
            FloatBuffer.wrap(stacked),
            longArrayOf(batches.toLong(), EMBEDDING_WINDOW.toLong(), bins.toLong(), 1),
        ).use { input ->
            embedding.run(mapOf("input_1" to input)).use { output ->
                (output.get("conv2d_19").orElseThrow() as OnnxTensor).floatBuffer.toFloatArray()
            }
        }
    }

    private fun appendHistory(next: FloatArray) {
        val maximum = WAKE_FRAMES * EMBEDDING_DIMENSION
        val nextStart = (next.size - maximum).coerceAtLeast(0)
        val nextSize = next.size - nextStart
        val combinedSize = (history.size + nextSize).coerceAtMost(maximum)
        val combined = FloatArray(combinedSize)
        val retained = (combinedSize - nextSize).coerceAtLeast(0)
        if (retained > 0) history.copyInto(combined, 0, history.size - retained, history.size)
        next.copyInto(combined, retained, nextStart, next.size)
        history.fill(0f)
        history = combined
    }

    override fun close() {
        history.fill(0f); stateH.fill(0f); stateC.fill(0f)
        vad.close(); spectrogram.close(); embedding.close(); wake.close()
    }

    private fun session(context: Context, path: String): OrtSession {
        val bytes = context.assets.open(path).use { it.readBytes() }
        return environment.createSession(bytes)
    }

    companion object {
        const val SAMPLE_RATE = 16_000
        const val WINDOW_SAMPLES = 17_280
        const val STRIDE_SAMPLES = 1_920
        private const val VAD_INTERVAL_SAMPLES = 1_920
        private const val EMBEDDING_DIMENSION = 96
        private const val EMBEDDING_WINDOW = 76
        private const val EMBEDDING_STRIDE = 8
        private const val WAKE_FRAMES = 16
    }
}

private fun FloatBuffer.toFloatArray(): FloatArray {
    rewind()
    return FloatArray(remaining()).also { get(it) }
}
