package com.example.myapplication

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import com.example.myapplication.ui.shell.AppRoot
import com.example.myapplication.ui.theme.MyApplicationTheme

/**
 * 唯一的 Activity（单 Activity 架构）。
 *
 * 官方架构建议：多屏时用单 Activity + 导航（我们当前只有"登录页 / 主界面"两个状态，
 * 还没到需要导航库的规模，所以由 `AppRoot` 条件渲染）。
 *
 * 这里**只做四件事**：拿依赖容器、开 edge-to-edge、套主题、调 [AppRoot]。
 * 任何业务逻辑都不该出现在 Activity 里 —— Activity 一旦被系统重建就全丢了，
 * 而 ViewModel 不会。
 */
class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        val container = (application as AiTutorApplication).container

        setContent {
            MyApplicationTheme {
                AppRoot(container = container)
            }
        }
    }
}
