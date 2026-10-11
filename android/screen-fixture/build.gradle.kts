plugins {
    id("com.android.application")
}

android {
    namespace = "nl.sander.aicockpit.screenfixture"
    compileSdk {
        version = release(37) { minorApiLevel = 0 }
    }
    defaultConfig {
        applicationId = "nl.sander.aicockpit.screenfixture"
        minSdk = 31
        targetSdk {
            version = release(37)
        }
        versionCode = 1
        versionName = "1.0"
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}
