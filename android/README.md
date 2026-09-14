# Jarvis Android companion

Jarvis targets Android 17/API 37. The native companion owns system-assistant invocation, local wake detection, GPT-Live audio, task/reminder notifications, and the phone WebView. AI Cockpit remains the durable source of truth.

## Build

1. Install Android Studio or the Android command-line SDK with API 37.0 and Build Tools 36.
2. Use Android Studio's bundled JDK, or JDK 17 or newer. The committed toolchain is verified with JDK 21 and Android Studio's bundled JDK 25.
3. Set `ANDROID_HOME` or add `sdk.dir=...` to an untracked `local.properties` file.
4. Install Android SDK Platform 36, then run `./gradlew testDebugUnitTest assembleDebug` (or `gradlew.bat` on Windows).

The wake models are read directly from `frontend/public/jarvis-wake`, so build the app from this monorepo rather than copying the Android directory in isolation.

## Pair and enable

1. Start AI Cockpit on an address reachable by the phone.
2. In AI Cockpit settings, generate a five-minute pairing code.
3. Enter the server URL and code in the Android app.
4. Grant microphone and notification access, then select Jarvis as the system assistant.
5. Start local wake listening. Resume-after-reboot remains off until explicitly enabled.

Device credentials are encrypted with Android Keystore. Background audio is evaluated locally and is never persisted. Foreground-app assist structure is opt-in, screenshots stay disabled, and mutating requests are blocked by the server while the device is locked.
