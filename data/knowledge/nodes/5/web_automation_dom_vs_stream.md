## 定义
在开发网页自动化工具（如自动化刷课、数据采集脚本）时，程序主要面临两种数据交互路径：**DOM解析**与**媒体流获取**。理解两者的区别是设计自动化方案的基础。

## 核心要点
1. **DOM解析（UI层交互）**
   - 原理：程序模拟浏览器内核，加载并解析网页的HTML文档对象模型（DOM树）。
   - 操作：通过CSS选择器、XPath等定位具体标签（如`<button>`, `<video>`），模拟点击、输入或调用JS方法触发播放。
   - 特点：能完整还原用户视觉界面，但执行效率较低，且易受前端框架更新或反爬机制影响。

2. **媒体流获取（数据层直连）**
   - 原理：绕过前端渲染层，直接监听或构造HTTP/HTTPS网络请求（XHR/Fetch/WebSocket）。
   - 操作：分析开发者工具中的Network面板，找到返回视频分片（如.m3u8, .mp4, .ts）的接口地址，直接下载或拼接数据流。
   - 特点：执行效率高、资源占用少、稳定性强，但需要掌握网络抓包、协议分析及可能的加密解密技术。

3. **选型建议**
   - 简单页面交互、测试环境验证 → 优先使用DOM解析。
   - 生产环境、高频任务、复杂流媒体 → 优先使用数据流直连。

## 示例
- **DOM方式**：使用Python Selenium库定位视频播放按钮并发送`click()`事件。
```python
video_btn = driver.find_element(By.CSS_SELECTOR, '.play-btn')
video_btn.click()
```
- **数据流方式**：使用Python Requests库直接下载视频接口返回的二进制数据。
```python
import requests
response = requests.get('https://example.com/video/stream.m3u8')
with open('course.mp4', 'wb') as f:
    f.write(response.content)
```
