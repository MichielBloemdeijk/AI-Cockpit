package nl.sander.aicockpit.jarvis.data

import java.net.URI
import java.net.URLDecoder

data class PairingLink(val server: String, val code: String) {
    companion object {
        fun serverOrigin(value: String): String {
            val uri = URI(value.trim().trimEnd('/'))
            require(uri.scheme in setOf("http", "https") && !uri.host.isNullOrBlank()) { "Use a valid HTTP or HTTPS Jarvis address." }
            require(uri.rawUserInfo == null && uri.rawQuery == null && uri.rawFragment == null && uri.rawPath.isNullOrEmpty()) { "Use the server address without credentials or a page path." }
            require(uri.port == -1 || uri.port in 1..65535) { "Invalid server port." }
            return uri.toASCIIString()
        }

        fun parse(value: String): PairingLink? = runCatching {
            val uri = URI(value)
            require(uri.scheme == "jarvis" && uri.host == "pair" && uri.rawUserInfo == null && uri.port == -1)
            require(uri.rawPath.isNullOrEmpty() && uri.rawFragment == null)
            val fields = uri.rawQuery.split('&').map { field ->
                val parts = field.split('=', limit = 2)
                require(parts.size == 2)
                URLDecoder.decode(parts[0], "UTF-8") to URLDecoder.decode(parts[1], "UTF-8")
            }
            require(fields.size == 2 && fields.map { it.first }.toSet() == setOf("server", "code"))
            val values = fields.toMap()
            val code = values.getValue("code")
            require(code.matches(Regex("[0-9]{6}")))
            PairingLink(serverOrigin(values.getValue("server")), code)
        }.getOrNull()
    }
}
