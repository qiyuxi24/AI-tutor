plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
}

// 显式 import：Kotlin DSL 里 `java` 被 JavaPluginExtension 遮蔽，
// 直接写 `java.util.Properties()` 会报 Unresolved reference 'util'。
import java.util.Properties

/**
 * 读 `local.properties` 里的键。
 *
 * ⚠️ **不能用 `project.findProperty`**：那只读 Gradle 属性（`gradle.properties`、
 * `-P` 参数），而 `local.properties` 是 Android 工具链自己的文件，不在其中
 * （AGP 只是用它来放 `sdk.dir`）。用它会让配置"看起来生效其实没生效"——
 * 实测传了 `api.base.url` 后 `BuildConfig` 里仍是默认值。
 *
 * 文件不存在（新 clone）时返回 null，由调用方兜底默认值 —— 不能让构建失败。
 */
fun localProperty(key: String): String? {
    val file = rootProject.file("local.properties")
    if (!file.exists()) return null
    val props = Properties()
    file.inputStream().use { props.load(it) }
    return props.getProperty(key)?.takeIf { it.isNotBlank() }
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
        // 取值优先级：local.properties 的 api.base.url > 下面这份默认值。
        // local.properties 已在 .gitignore，各人填自己的，提交时不会互相覆盖。
        val apiBaseUrl = localProperty("api.base.url")
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