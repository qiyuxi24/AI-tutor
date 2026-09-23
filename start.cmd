@echo off
REM ============================================================
REM  TutorAgent 双击启动入口
REM
REM  为什么需要它：本机 .ps1 没有文件关联（assoc .ps1 返回 not found），
REM  双击 start.ps1 不会执行，右键菜单里也没有「使用 PowerShell 运行」。
REM  所以用 .cmd 包一层，双击本文件即可。
REM
REM  本文件刻意只用 ASCII：cmd.exe 按 OEM 代码页(936)读取 .cmd，
REM  写中文注释/回显会乱码，甚至可能因字节被误解析而报错。
REM ============================================================

chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"

REM 脚本非正常退出时停一下，否则窗口一闪而过看不到报错
if errorlevel 1 pause
