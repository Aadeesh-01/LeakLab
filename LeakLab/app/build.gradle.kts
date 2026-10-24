plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.iiitv.leaklab"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.iiitv.leaklab"
        minSdk = 24
        targetSdk = 35
        versionCode = 1
        versionName = "1.0"
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    // LeakCanary: the library this project extends.
    // (Real apps use debugImplementation so it is not shipped to users; we keep it simple.)
    implementation("com.squareup.leakcanary:leakcanary-android:2.14")
}
