package com.example.myapplication

import android.app.Application
import com.example.myapplication.data.AppContainer

/**
 * 持有进程级依赖容器。
 *
 * 只做一件事：把 [AppContainer] 挂到 Application 上，让 Activity / ViewModel
 * 都能拿到同一份实例。不在这里做初始化逻辑（没有"启动就要跑"的任务）。
 *
 * 若将来需要初始化（例如预热连接、读配置），加到 [onCreate] 并确认它是**同步且快**的：
 * Application.onCreate 里做耗时操作会直接拉长冷启动。
 */
class AiTutorApplication : Application() {

    lateinit var container: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        container = AppContainer(this)
    }
}
