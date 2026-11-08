# ui/theme —— 全局样式层（唯一真值）

改样式**只改这个目录**，不要在页面/组件里写色值、字号、圆角、间距。

## 四个文件各管一件事

| 文件 | 内容 | 只管 |
|---|---|---|
| `Color.kt` | `object Palette` | 色值（十六进制）。深/浅两档并列。 |
| `Type.kt` | `val AppTypography` | 字号 / 行高 / 字重 / 字间距。槽位见下表。 |
| `Shape.kt` | `val AppShapes` + `object AppDimens` | 圆角；尺寸（留白、行高、栏高、环半径…）。 |
| `Theme.kt` | `AppColors` / `AppTheme` / `MyApplicationTheme` | 把上面三份映射成 M3 的 `ColorScheme`，并提供 `AppTheme.colors` 入口。 |

## 在组件里怎么用（三种，别混）

```kotlin
// 1) 设计稿专有色值（M3 没有的槽位）：页面底、卡片、hairline、输入卡
AppTheme.colors.page / .card / .line / .inputBg / .soft

// 2) 文字层级：走 M3 的 typography 槽位
Text("进度", style = MaterialTheme.typography.headlineSmall)

// 3) 尺寸：走常量，不写字面量
Modifier.padding(AppDimens.ScreenPadding).height(AppDimens.RowHeight)
```

## 三条硬约定

1. **不在页面里写 `Color(0xFF…)`** —— 需要新颜色先加进 `Palette`，再在 `Theme.kt` 的 `AppColors` 里加字段。加字段要同时补 `LightAppColors` 与 `DarkAppColors` 两处，否则深色档编译不过。
2. **不用 M3 的自带组件外观**（`Button` / `OutlinedTextField` / `NavigationBar` / `Card` 的默认 elevation 与填充）—— 设计稿是「无阴影 + 大圆角 + 白卡通栏」，直接用默认值会多出阴影和 80dp 高的底栏。用 `Surface`/`Box` + `Modifier.clip(AppShapes.…)` + `background(colors.card)` 自己拼。
3. **`dynamicColor` 已删除且不再暴露** —— 原模板 `true` 会在 Android 12+ 用壁纸取色覆盖紫色，是「改了样式不生效」的经典假象。不要把它加回来。

## 字号槽位对照（设计稿 → M3 槽位）

| 用途 | 槽位 | 字号 |
|---|---|---|
| 问候语「你好，朱智虚」 | `displaySmall` | 30sp / 40 |
| 屏标题「进度 / 知识图谱」 | `headlineSmall` | 23sp / 30 |
| 卡片标题 | `titleMedium` / `titleSmall` | 17 / 16 |
| 正文、题干 | `bodyLarge` | 16sp / 26 |
| 次文字、说明 | `bodyMedium` | 13sp / 20 |
| 按钮、标签 | `labelLarge` / `labelMedium` | 16 / 13 |
| 底栏 tab 标签 | `labelSmall` | 9.5sp / 13 |

字体一律 `FontFamily.Default`（系统无衬线）；不打包 ttf —— 中文字体 3–10MB 且 Compose 不能子集化。

## 与预览页的对应

色值与尺寸逐项对齐 `Android/preview/style-preview-purple.html` 顶部那组 CSS 变量。
主色 `#8B5CF6` 同时等于 Web 端 `frontend/src/style.css` 的 `--primary` —— **两边要一起改**，只改一边会出现同一个产品两种紫。
