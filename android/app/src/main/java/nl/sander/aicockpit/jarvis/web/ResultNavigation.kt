package nl.sander.aicockpit.jarvis.web

import java.net.URI

/** Pure navigation policy, independently testable without an Android UI. */
object ResultNavigation {
    enum class Destination { INTERNAL, EXTERNAL, BLOCKED }

    fun destination(origin: String, target: String): Destination = try {
        val base = URI(origin)
        val uri = URI(target)
        fun port(value: URI) = if (value.port >= 0) value.port else if (value.scheme.equals("https", true)) 443 else 80
        if (uri.scheme?.lowercase() !in setOf("http", "https") || uri.host == null || uri.userInfo != null) Destination.BLOCKED
        else if (uri.scheme.equals(base.scheme, true) && uri.host.equals(base.host, true) && port(uri) == port(base)) Destination.INTERNAL
        else Destination.EXTERNAL
    } catch (_: Exception) { Destination.BLOCKED }

    fun actionLabel(path: String?): String =
        if (path?.matches(Regex("^/results/[^/?#]+(?:[?#].*)?$")) == true) "Open result" else "Open task"
}
