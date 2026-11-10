plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
}

android {
    namespace = "com.example.myapplication"
    compileSdk {
        version = release(36) {
            minorApiLevel = 1
        }
    }

    defaultConfig {
        applicationId = "com.example.myapplication"
        minSdk = 24
        targetSdk = 36
        versionCode = 1
        versionName = "1.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        // 后端地址。**不硬编码在 Kotlin 里** —— 每人环境不同（模拟器 / 真机 / 同事的电脑）。
        // 取值：local.properties 里写 `api.base.url=http://...` 即可覆盖下面这份默认值。
        // local.properties 已在 .gitignore，各人填自己的，提交时不会互相覆盖。
        val apiBaseUrl = (project.findProperty("api.base.url") as String?)
            ?: "http://10.0.2.2:8000"   // 10.0.2.2 = 模拟器访问宿主机的固定地址
        buildConfigField("String", "API_BASE_URL", "\"$apiBaseUrl\"")
    }

    buildTypes {
        release {
            optimization {
                enable = false
            }
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }
    buildFeatures {
        compose = true
        // 生成 BuildConfig（上面的 API_BASE_URL 靠它下发）。
        // AGP 8 起默认关闭，不显式打开会报 "Unresolved reference: BuildConfig"。
        buildConfig = true
    }
}

dependencies {
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.graphics)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    // 底栏 5 个 tab 与设置项要用图标；只引 extended，不用再单独引 core（它是它的依赖）
    implementation(libs.androidx.compose.material.icons.extended)
    // SSE 读流 + viewModelScope 都依赖协程；显式声明，不靠传递依赖
    implementation(libs.kotlinx.coroutines.android)
    // JWT 加密落盘（EncryptedSharedPreferences）。别退回明文 SharedPreferences
    implementation(libs.androidx.security.crypto)
    // 网络层：REST 与 SSE 共用同一个 OkHttpClient（不引 Retrofit，理由见 libs.versions.toml）
    implementation(libs.okhttp)
    implementation(libs.gson)
    // 日志拦截器只在 debug 变体：release 包里不能出现请求/响应体日志
    debugImplementation(libs.okhttp.logging)
    testImplementation(libs.junit)
    androidTestImplementation(platform(libs.androidx.compose.bom))
    androidTestImplementation(libs.androidx.compose.ui.test.junit4)
    androidTestImplementation(libs.androidx.espresso.core)
    androidTestImplementation(libs.androidx.junit)
    debugImplementation(libs.androidx.compose.ui.test.manifest)
    debugImplementation(libs.androidx.compose.ui.tooling)
}