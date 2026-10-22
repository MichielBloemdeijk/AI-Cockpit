import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
}

// In-app updates only install over an APK signed with the same key, so the
// release key lives outside the repo and must never change. See README.
val releaseSigning = Properties().apply {
    val path = providers.environmentVariable("JARVIS_SIGNING_PROPERTIES").orNull
        ?: "${System.getProperty("user.home")}/.jarvis/android-signing.properties"
    file(path).takeIf { it.isFile }?.inputStream()?.use(::load)
}

android {
    namespace = "nl.sander.aicockpit.jarvis"
    compileSdk {
        version = release(37) { minorApiLevel = 0 }
    }

    defaultConfig {
        applicationId = "nl.sander.aicockpit.jarvis"
        minSdk = 31
        targetSdk {
            version = release(37)
        }
        // The publish script supplies an increasing code; local builds stay at 1.
        versionCode = providers.gradleProperty("jarvisVersionCode").orNull?.toInt() ?: 1
        versionName = providers.gradleProperty("jarvisVersionName").orNull ?: "dev"
    }

    signingConfigs {
        if (releaseSigning.isNotEmpty()) create("release") {
            storeFile = file(releaseSigning.getProperty("storeFile"))
            storePassword = releaseSigning.getProperty("storePassword")
            keyAlias = releaseSigning.getProperty("keyAlias")
            keyPassword = releaseSigning.getProperty("keyPassword")
        }
    }
    buildTypes {
        release { signingConfig = signingConfigs.findByName("release") }
    }

    buildFeatures { compose = true }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    sourceSets["main"].assets.directories.add("../../frontend/public/jarvis-wake")
    packaging.resources.excludes += setOf("META-INF/DEPENDENCIES", "META-INF/LICENSE*", "META-INF/NOTICE*")
}

dependencies {
    implementation(platform("androidx.compose:compose-bom:2025.05.01"))
    implementation("androidx.activity:activity-compose:1.10.1")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.core:core-ktx:1.16.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.9.1")
    implementation("androidx.lifecycle:lifecycle-service:2.9.1")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.9.1")
    implementation("androidx.webkit:webkit:1.13.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.2")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("com.squareup.okhttp3:okhttp-sse:4.12.0")
    implementation("com.microsoft.onnxruntime:onnxruntime-android:1.20.0")
    implementation("io.github.webrtc-sdk:android:144.7559.14")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20250517")
    debugImplementation("androidx.compose.ui:ui-tooling")
}
