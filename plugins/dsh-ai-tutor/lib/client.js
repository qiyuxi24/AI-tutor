/**
 * AI-tutor 的 DSH Web 客户端插件（纯 JS + React.createElement，零构建）。
 *
 * 加载方式：DSH 的客户端插件不是 ESM import，而是宿主注入的 window.__ModuleLoader__
 * 主动 load 一份 factory（先例：dsh-context / modlens 的 lib/client.js 都是手写的）。
 * 所以本文件没有 import/export 语句，只依赖 factory 参数里的 require('react')。
 *
 * 两个面板，每个面板一对注册（契约来自 DSH 源码
 * packages/client/ui-sidebar/.../contract/slots.ts）：
 *   main             keyed 面板：真正的面板体（key = 面板 id，与侧栏图标 id 一致）
 *   sidebar.panellist 侧栏图标列表：owner props = { size, active }，只负责画图标
 * 计划原文只注册了 panellist 并把面板体当图标传进去 —— 那样点开是空的；这里按
 * dsh-context 的真实写法拆成两处。
 *
 *   「知识图谱」ai-tutor-graph（只读摘要）：同源 GET /ai-tutor/graph
 *     （index.js 注册的宿主路由 → 本地 Python），15 秒轮询 + 手动刷新。
 *   「AI 导师」ai-tutor-app（完整应用）：同源 GET /ai-tutor/panel 拿到本机 URL
 *     （127.0.0.1 随机端口）后，用全屏 iframe 打开完整面板 —— 可视化图谱、文件上传、
 *     图谱生成、RAG、出题、对话都在 iframe 里；工具条提供刷新与「在浏览器打开」。
 *
 * 出错只显示在面板内（含 501/502 的可操作文案），绝不让渲染错误把宿主一起带崩
 * （ErrorBoundary 兜底）。
 */

window.__ModuleLoader__.load({
  id: 'dsh-ai-tutor',
  factory: function (require) {
    var module = { exports: {} }
    var exports = module.exports

    var React = require('react')
    var h = React.createElement

    /** 面板 id：同时是 main 的 key 与 sidebar.panellist 的 id（必须一致，否则点图标没面板）。 */
    var PANEL_ID = 'ai-tutor-graph'
    var PANEL_LABEL = '知识图谱'
    var PANEL_ORDER = 40
    var GRAPH_ENDPOINT = '/ai-tutor/graph'
    /** 完整面板：宿主路由返回 iframe 要打开的本机 URL。 */
    var APP_PANEL_ID = 'ai-tutor-app'
    var APP_PANEL_LABEL = 'AI 导师'
    var APP_PANEL_ORDER = 39
    var APP_ENDPOINT = '/ai-tutor/panel'
    /** 完整应用冷启动要 import FastAPI 全家桶，首屏给足时间（服务端启动上限 30s）。 */
    var APP_REQUEST_TIMEOUT_MS = 45000
    var POLL_INTERVAL_MS = 15000
    var REQUEST_TIMEOUT_MS = 20000
    var MAX_SUBJECTS = 20
    var MAX_NODES_PER_SUBJECT = 12

    // 颜色用 rgba + inherit 适配明暗主题：文字继承宿主前景色，只有掌握度条自带色相
    var COLOR = {
      muted: 'rgba(127, 127, 127, 0.78)',
      faint: 'rgba(127, 127, 127, 0.55)',
      line: 'rgba(127, 127, 127, 0.28)',
      barBg: 'rgba(127, 127, 127, 0.20)',
      low: '#d9734f',
      mid: '#d9a441',
      high: '#3f9e6a',
      errorBg: 'rgba(217, 115, 79, 0.10)',
      errorBorder: 'rgba(217, 115, 79, 0.55)',
    }

    // ---------------------------------------------------------------- 小工具

    function pickText() {
      for (var i = 0; i < arguments.length; i += 1) {
        var value = arguments[i]
        if (typeof value === 'string' && value.trim().length > 0) return value.trim()
        if (typeof value === 'number' && isFinite(value)) return String(value)
      }
      return ''
    }

    function toNumber(value) {
      if (typeof value === 'number') return isFinite(value) ? value : null
      if (typeof value === 'string' && value.trim().length > 0) {
        var parsed = Number(value)
        return isFinite(parsed) ? parsed : null
      }
      return null
    }

    /**
     * 掌握度统一成 **0..100 的百分数**（后端契约就是 0~100 的整数，直接当百分比用）。
     * 只有"小数形状"（0 < v < 1，如旧的 0.42）才 ×100，避免把 mastery=1 误读成 100%。
     */
    function normalizePercent(value) {
      var number = toNumber(value)
      if (number === null) return null
      if (number > 0 && number < 1) number = number * 100
      if (number < 0) number = 0
      if (number > 100) number = 100
      return number
    }

    function formatClock(timestamp) {
      if (!timestamp) return ''
      var date = new Date(timestamp)
      if (isNaN(date.getTime())) return ''
      var pad = function (value) { return value < 10 ? '0' + value : String(value) }
      return pad(date.getHours()) + ':' + pad(date.getMinutes()) + ':' + pad(date.getSeconds())
    }

    /** value 是 0..100 的百分数。 */
    function masteryColor(value) {
      if (value === null) return COLOR.faint
      if (value < 40) return COLOR.low
      if (value < 70) return COLOR.mid
      return COLOR.high
    }

    /**
     * 只接受**本机回环**地址再放进 iframe：url 来自宿主路由，但客户端不该把任意
     * 远端地址 iframe 进来（防被其他插件/配置劫持）。
     */
    function normalizePanelUrl(value) {
      var text = typeof value === 'string' ? value.trim() : ''
      var matched = /^http:\/\/(127\.0\.0\.1|localhost):(\d{1,5})\/?$/.exec(text)
      if (!matched) return ''
      var port = Number(matched[2])
      if (!(port > 0 && port < 65536)) return ''
      return 'http://' + matched[1] + ':' + port
    }

    // ------------------------------------------------------- 数据形状归一化
    // 后端契约：{ subjects:[{name,nodeCount,masteredCount,nodes:[{id,title,mastery,board}]}],
    // nodeCount, next:{node_id,name,mastery,reason,subject}|null, dataRoot }，mastery 是 0~100。
    // 同时也对常见别名做容错（name/title、nodeCount/node_count、next/next_to_learn）。

    function normalizeNode(node, index) {
      if (node === null || node === undefined) return null
      if (typeof node === 'string') {
        return { id: 'node-' + index + '-' + node, title: node, mastery: null }
      }
      if (typeof node !== 'object') return null
      var title = pickText(node.title, node.name, node.node_name, node.label, node.id, node.node_id)
      var id = pickText(node.id, node.node_id, node.key, node.uid) || (title + '#' + index)
      if (!title) title = '知识点 ' + (index + 1)
      return { id: id, title: title, mastery: normalizePercent(node.mastery) }
    }

    function extractNext(source) {
      if (!source || typeof source !== 'object') return null
      var candidates = [source.next, source.next_to_learn, source.nextStep, source.recommendation]
      for (var i = 0; i < candidates.length; i += 1) {
        var value = candidates[i]
        if (!value) continue
        if (typeof value === 'string') {
          var text = value.trim()
          if (text) return { title: text, reason: '', mastery: null }
          continue
        }
        if (typeof value === 'object') {
          var title = pickText(value.title, value.name, value.node_name, value.node)
          if (!title) continue
          return {
            title: title,
            reason: pickText(value.reason, value.why, value.description),
            subject: pickText(value.subject, value.domain),
            mastery: normalizePercent(value.mastery),
          }
        }
      }
      return null
    }

    function normalizeGraphData(payload) {
      var raw = payload && typeof payload === 'object' ? payload : {}
      var container = raw.data && typeof raw.data === 'object' && !Array.isArray(raw.data) ? raw.data : raw
      var subjectSource = container.subjects
      if (!Array.isArray(subjectSource) && subjectSource && typeof subjectSource === 'object') {
        subjectSource = Object.keys(subjectSource).map(function (name) {
          var nodes = subjectSource[name]
          return { name: name, nodes: Array.isArray(nodes) ? nodes : [] }
        })
      }
      if (!Array.isArray(subjectSource) && Array.isArray(raw.subjects)) subjectSource = raw.subjects
      var subjects = (Array.isArray(subjectSource) ? subjectSource : []).map(function (subject, index) {
        var object = subject && typeof subject === 'object' ? subject : { name: String(subject) }
        var name = pickText(object.name, object.subject, object.title, object.label) || ('学科 ' + (index + 1))
        var nodeSource = object.nodes || object.children || object.items || object.points
        var nodes = (Array.isArray(nodeSource) ? nodeSource : []).map(normalizeNode).filter(Boolean)
        var subjectNodeCount = toNumber(pickText(object.nodeCount, object.node_count, object.total))
        var masteredCount = toNumber(pickText(object.masteredCount, object.mastered_count, object.mastered))
        if (masteredCount === null) {
          masteredCount = nodes.filter(function (node) { return node.mastery !== null && node.mastery >= 50 }).length
        }
        return {
          name: name,
          nodes: nodes,
          nodeCount: subjectNodeCount === null ? nodes.length : subjectNodeCount,
          masteredCount: masteredCount,
          next: extractNext(object),
        }
      })
      var computed = subjects.reduce(function (sum, subject) { return sum + subject.nodes.length }, 0)
      var provided = toNumber(pickText(container.nodeCount, container.node_count, container.total))
      return {
        subjects: subjects,
        nodeCount: provided === null ? computed : provided,
        next: extractNext(container),
        dataRoot: pickText(container.dataRoot, container.data_root),
      }
    }

    // ------------------------------------------------------------- 图形资产

    function GraphIcon(props) {
      var size = props && typeof props.size === 'number' && props.size > 0 ? props.size : 16
      var active = Boolean(props && props.active)
      return h(
        'svg',
        {
          width: size,
          height: size,
          viewBox: '0 0 24 24',
          fill: 'none',
          stroke: 'currentColor',
          strokeWidth: 1.6,
          strokeLinecap: 'round',
          strokeLinejoin: 'round',
          style: { opacity: active ? 1 : 0.78, display: 'block' },
          'aria-hidden': 'true',
        },
        h('circle', { cx: 12, cy: 5.5, r: 2.4 }),
        h('circle', { cx: 5.5, cy: 17, r: 2.4 }),
        h('circle', { cx: 18.5, cy: 17, r: 2.4 }),
        h('path', { d: 'M10.8 7.7 7.1 14.6' }),
        h('path', { d: 'M13.2 7.7 16.9 14.6' }),
        h('path', { d: 'M8 17h8' }),
      )
    }

    /** 「AI 导师」侧栏图标：一块面板 + 一颗星（与图谱图标区分）。 */
    function AppIcon(props) {
      var size = props && typeof props.size === 'number' && props.size > 0 ? props.size : 16
      var active = Boolean(props && props.active)
      return h(
        'svg',
        {
          width: size,
          height: size,
          viewBox: '0 0 24 24',
          fill: 'none',
          stroke: 'currentColor',
          strokeWidth: 1.6,
          strokeLinecap: 'round',
          strokeLinejoin: 'round',
          style: { opacity: active ? 1 : 0.78, display: 'block' },
          'aria-hidden': 'true',
        },
        h('rect', { x: 3.5, y: 4.5, width: 17, height: 15, rx: 2.6 }),
        h('path', { d: 'M7.4 9.4h5.4' }),
        h('path', { d: 'M7.4 12.6h3.6' }),
        h('path', { d: 'M15.9 9.2l.9 2 2 .9-2 .9-.9 2-.9-2-2-.9 2-.9z' }),
      )
    }

    // ------------------------------------------------------------ 错误兜底

    /** 渲染错误只毁这个面板，不冒泡到宿主（React 错误边界）。 */
    function PanelErrorBoundary(props) {
      React.Component.call(this, props)
      this.state = { error: null }
    }
    PanelErrorBoundary.prototype = Object.create(React.Component.prototype)
    PanelErrorBoundary.prototype.constructor = PanelErrorBoundary
    PanelErrorBoundary.getDerivedStateFromError = function (error) {
      return { error: error }
    }
    PanelErrorBoundary.prototype.componentDidCatch = function (error, info) {
      console.error('[ai-tutor] 面板渲染失败：', error, info)
    }
    PanelErrorBoundary.prototype.render = function () {
      if (this.state.error) {
        // props.renderError：不同面板用各自的错误文案（完整面板 / 图谱面板）
        if (typeof this.props.renderError === 'function') return this.props.renderError(this.state.error)
        return renderError({
          message: '面板渲染失败：' + (this.state.error && this.state.error.message ? this.state.error.message : String(this.state.error)),
          hint: '这是前端渲染错误，不影响模型侧工具。把浏览器控制台里的报错贴出来即可定位。',
          status: 0,
        })
      }
      return this.props.children
    }

    // --------------------------------------------------------------- 样式

    function rootStyle() {
      return {
        height: '100%',
        width: '100%',
        boxSizing: 'border-box',
        overflowY: 'auto',
        padding: '14px 16px 28px',
        color: 'inherit',
        fontFamily: 'inherit',
        fontSize: 13,
        lineHeight: 1.5,
      }
    }

    function buttonStyle(busy) {
      return {
        flex: '0 0 auto',
        fontSize: 11,
        padding: '4px 10px',
        borderRadius: 8,
        border: '1px solid ' + COLOR.line,
        background: 'transparent',
        color: 'inherit',
        cursor: busy ? 'default' : 'pointer',
        opacity: busy ? 0.55 : 1,
      }
    }

    function mutedTextStyle(extra) {
      var style = { fontSize: 11, color: COLOR.muted, lineHeight: 1.5 }
      if (extra) Object.keys(extra).forEach(function (key) { style[key] = extra[key] })
      return style
    }

    // ------------------------------------------------------------- 渲染片段

    function renderLoading() {
      return h('div', { style: mutedTextStyle({ padding: '18px 0' }) }, '正在读取本地图谱…')
    }

    function renderEmpty(data) {
      return h(
        'div',
        { style: { padding: '16px 14px', border: '1px dashed ' + COLOR.line, borderRadius: 10 } },
        h('div', { style: { fontSize: 13, fontWeight: 600, marginBottom: 6 } }, '图谱还是空的'),
        h('div', { style: mutedTextStyle() }, '让模型用 kg_snapshot 工具读一次，或先在 AI-tutor 里导入资料 / 建图；有内容后这里会自动刷新。'),
        h('div', { style: mutedTextStyle({ marginTop: 8 }) }, '数据只在本机：插件不上传任何内容，也不改你的库。'),
        data && data.dataRoot
          ? h('div', { style: mutedTextStyle({ marginTop: 8 }) }, '当前数据根：' + data.dataRoot)
          : null,
      )
    }

    function renderError(error) {
      var statusText = error && error.status ? '（HTTP ' + error.status + '）' : ''
      return h(
        'div',
        {
          style: {
            margin: '0 0 12px',
            padding: '10px 12px',
            border: '1px solid ' + COLOR.errorBorder,
            borderRadius: 10,
            background: COLOR.errorBg,
          },
          role: 'alert',
        },
        h('div', { style: { fontSize: 12, fontWeight: 600, marginBottom: 4 } }, '图谱读取失败' + statusText),
        h('div', { style: { fontSize: 12, lineHeight: 1.55, wordBreak: 'break-word' } }, error && error.message ? error.message : '未知错误'),
        error && error.hint ? h('div', { style: mutedTextStyle({ marginTop: 6, wordBreak: 'break-word' }) }, error.hint) : null,
        h('div', { style: mutedTextStyle({ marginTop: 6 }) }, '模型侧工具不受影响；排查可在插件目录运行 node bin/doctor.mjs。'),
      )
    }

    function renderMasteryBar(value, width) {
      var percent = value === null ? 0 : Math.round(value)
      return h(
        'div',
        {
          style: {
            flex: '0 0 ' + width + 'px',
            height: 5,
            borderRadius: 999,
            background: COLOR.barBg,
            overflow: 'hidden',
          },
          role: 'progressbar',
          'aria-valuemin': 0,
          'aria-valuemax': 100,
          'aria-valuenow': percent,
        },
        h('div', {
          style: {
            width: percent + '%',
            height: '100%',
            background: masteryColor(value),
            borderRadius: 999,
          },
        }),
      )
    }

    function renderNodeRow(node) {
      return h(
        'div',
        {
          key: 'node:' + node.id,
          style: { display: 'flex', alignItems: 'center', gap: 8, padding: '3px 0' },
          title: node.title,
        },
        h(
          'div',
          {
            style: {
              flex: '1 1 auto',
              minWidth: 0,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              fontSize: 12,
            },
          },
          node.title,
        ),
        renderMasteryBar(node.mastery, 84),
        h(
          'div',
          {
            style: {
              flex: '0 0 36px',
              textAlign: 'right',
              fontSize: 11,
              color: COLOR.muted,
              fontVariantNumeric: 'tabular-nums',
            },
          },
          node.mastery === null ? '—' : Math.round(node.mastery) + '%',
        ),
      )
    }

    function renderNext(next) {
      if (!next) return null
      return h(
        'div',
        {
          style: {
            marginTop: 14,
            padding: '10px 12px',
            border: '1px solid ' + COLOR.line,
            borderRadius: 10,
            background: 'rgba(127, 127, 127, 0.06)',
          },
        },
        h('div', { style: mutedTextStyle({ marginBottom: 4 }) }, '下一步'),
        h('div', { style: { fontSize: 13, fontWeight: 600 } }, next.title),
        next.subject ? h('div', { style: mutedTextStyle({ marginTop: 2 }) }, '学科：' + next.subject) : null,
        next.reason ? h('div', { style: mutedTextStyle({ marginTop: 4 }) }, next.reason) : null,
        next.mastery !== null
          ? h('div', { style: { display: 'flex', alignItems: 'center', gap: 8, marginTop: 6 } },
              renderMasteryBar(next.mastery, 120),
              h('span', { style: mutedTextStyle() }, '掌握度 ' + Math.round(next.mastery) + '%'))
          : null,
      )
    }

    function renderSubject(subject) {
      var hidden = Math.max(0, subject.nodes.length - MAX_NODES_PER_SUBJECT)
      return h(
        'section',
        { key: 'subject:' + subject.name, style: { marginBottom: 18 } },
        h(
          'div',
          { style: { display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 8, marginBottom: 6 } },
          h('div', { style: { fontWeight: 600, fontSize: 13 } }, subject.name),
          h(
            'div',
            { style: mutedTextStyle() },
            subject.nodeCount + ' 个知识点' +
              (subject.masteredCount === null ? '' : ' · 已掌握 ' + subject.masteredCount),
          ),
        ),
        subject.nodes.length === 0
          ? h('div', { style: mutedTextStyle({ padding: '2px 0' }) }, '这个学科还没有知识点。')
          : subject.nodes.slice(0, MAX_NODES_PER_SUBJECT).map(renderNodeRow),
        hidden > 0 ? h('div', { style: mutedTextStyle({ marginTop: 4 }) }, '还有 ' + hidden + ' 个知识点未显示') : null,
        subject.next ? h('div', { style: mutedTextStyle({ marginTop: 4 }) }, '建议下一步：' + subject.next.title) : null,
      )
    }

    function renderHeader(state, onRefresh) {
      var busy = state.status === 'loading' || state.status === 'refreshing'
      var meta = []
      if (state.data) meta.push(state.data.nodeCount + ' 个知识点')
      meta.push('每 ' + Math.round(POLL_INTERVAL_MS / 1000) + ' 秒自动刷新')
      if (state.fetchedAt) meta.push('更新于 ' + formatClock(state.fetchedAt))
      return h(
        'div',
        { style: { display: 'flex', alignItems: 'flex-start', gap: 10, marginBottom: 12 } },
        h(
          'div',
          { style: { flex: '1 1 auto', minWidth: 0 } },
          h('div', { style: { fontSize: 15, fontWeight: 600 } }, PANEL_LABEL),
          h('div', { style: mutedTextStyle({ marginTop: 3 }) }, meta.join(' · ')),
        ),
        h('button', { type: 'button', onClick: busy ? undefined : onRefresh, disabled: busy, title: '立即刷新', style: buttonStyle(busy) }, busy ? '刷新中…' : '刷新'),
      )
    }

    function renderPanel(state, onRefresh) {
      var data = state.data
      var children = [renderHeader(state, onRefresh)]
      if (state.error) children.push(renderError(state.error))
      var isEmpty = !data || (data.subjects.length === 0 && data.nodeCount === 0)
      if (!data) {
        if (!state.error) children.push(renderLoading())
      } else if (isEmpty) {
        children.push(renderEmpty(data))
      } else {
        if (data.subjects.length > 0) {
          data.subjects.slice(0, MAX_SUBJECTS).forEach(function (subject) {
            children.push(renderSubject(subject))
          })
          if (data.subjects.length > MAX_SUBJECTS) {
            children.push(h('div', { key: 'more', style: mutedTextStyle() }, '还有 ' + (data.subjects.length - MAX_SUBJECTS) + ' 个学科未显示'))
          }
        } else if (data.nodeCount > 0) {
          children.push(h('div', { style: mutedTextStyle() }, '图谱里有 ' + data.nodeCount + ' 个知识点，但这次没有拿到学科分组。'))
        }
        if (data.next) children.push(renderNext(data.next))
      }
      children.push(h('div', { key: 'privacy', style: mutedTextStyle({ marginTop: 18, paddingTop: 10, borderTop: '1px solid ' + COLOR.line }) }, '只读 · 数据只在本机 · 不上传'))
      return h('div', { style: rootStyle(), role: 'region', 'aria-label': PANEL_LABEL }, children)
    }

    // ------------------------------------------------------------- 数据获取

    function GraphPanel() {
      var stateTuple = React.useState({ status: 'loading', data: null, error: null, fetchedAt: 0 })
      var state = stateTuple[0]
      var setState = stateTuple[1]
      var mountedRef = React.useRef(true)

      var load = React.useCallback(function () {
        if (!mountedRef.current) return
        if (typeof fetch !== 'function') {
          setState(function (prev) {
            return { status: 'error', data: prev.data, error: { message: '宿主浏览器不支持 fetch，面板无法读取图谱。', hint: '', status: 0 }, fetchedAt: prev.fetchedAt }
          })
          return
        }
        var controller = null
        try {
          if (typeof AbortController === 'function') controller = new AbortController()
        } catch (error) {
          controller = null
        }
        var timer = controller ? setTimeout(function () {
          try { controller.abort() } catch (error) { /* 已结束 */ }
        }, REQUEST_TIMEOUT_MS) : null
        var finish = function () { if (timer) { clearTimeout(timer); timer = null } }

        setState(function (prev) {
          return { status: prev.data ? 'refreshing' : 'loading', data: prev.data, error: null, fetchedAt: prev.fetchedAt }
        })

        fetch(GRAPH_ENDPOINT, {
          method: 'GET',
          headers: { accept: 'application/json' },
          cache: 'no-store',
          signal: controller ? controller.signal : undefined,
        })
          .then(function (response) {
            return response.text().then(function (text) {
              var payload = null
              try { payload = text ? JSON.parse(text) : null } catch (error) { payload = null }
              if (!response.ok) {
                var detail = payload && typeof payload === 'object'
                  ? String(payload.error || payload.detail || payload.message || ('HTTP ' + response.status))
                  : 'HTTP ' + response.status
                var failure = new Error(detail)
                failure.status = response.status
                failure.hint = payload && typeof payload === 'object' && payload.hint ? String(payload.hint) : ''
                throw failure
              }
              return payload
            })
          })
          .then(function (payload) {
            finish()
            if (!mountedRef.current) return
            setState({ status: 'ready', data: normalizeGraphData(payload), error: null, fetchedAt: Date.now() })
          })
          .catch(function (error) {
            finish()
            if (!mountedRef.current) return
            var aborted = error && error.name === 'AbortError'
            setState(function (prev) {
              return {
                status: prev.data ? 'ready' : 'error',
                data: prev.data,
                error: {
                  message: aborted
                    ? '请求超时（超过 ' + Math.round(REQUEST_TIMEOUT_MS / 1000) + ' 秒没有响应）'
                    : (error && error.message ? error.message : String(error)),
                  hint: error && error.hint ? error.hint : '',
                  status: error && error.status ? error.status : 0,
                },
                fetchedAt: prev.fetchedAt,
              }
            })
          })
      }, [])

      React.useEffect(function () {
        mountedRef.current = true
        load()
        var interval = setInterval(load, POLL_INTERVAL_MS)
        return function () {
          mountedRef.current = false
          clearInterval(interval)
        }
      }, [load])

      return renderPanel(state, load)
    }

    // -------------------------------------------------- 完整应用（iframe）

    /**
     * 「AI 导师」面板的错误视图：不是塌掉整块，而是给出**可操作**的中文提示
     * （完整面板缺产物 → 501 / 起不来 → 502 都走这里）。
     */
    function renderAppError(error) {
      var statusText = error && error.status ? '（HTTP ' + error.status + '）' : ''
      var message = error && error.message ? String(error.message) : '未知错误'
      var hint = error && error.hint ? String(error.hint) : ''
      return h(
        'div',
        {
          role: 'alert',
          style: {
            maxWidth: 760,
            padding: '12px 14px',
            border: '1px solid ' + COLOR.errorBorder,
            borderRadius: 10,
            background: COLOR.errorBg,
          },
        },
        h('div', { style: { fontSize: 13, fontWeight: 600, marginBottom: 6 } }, APP_PANEL_LABEL + '面板打不开' + statusText),
        h('div', { style: { fontSize: 12, lineHeight: 1.6, wordBreak: 'break-word' } }, message),
        hint ? h('div', { style: mutedTextStyle({ marginTop: 6, wordBreak: 'break-word' }) }, hint) : null,
        h(
          'div',
          { style: mutedTextStyle({ marginTop: 8, wordBreak: 'break-word' }) },
          '完整面板是一个只在本机运行的应用（在 iframe 内打开）。排查：在插件目录运行 node bin/doctor.mjs（第 11、12 项）；确认 panel/ 已构建、本机端口没有被安全软件拦截。',
        ),
        h('div', { style: mutedTextStyle({ marginTop: 4 }) }, '模型侧工具与「知识图谱」面板不受影响。'),
      )
    }

    /** 顶部小工具条：标题 + 本机地址 + 刷新 / 在浏览器打开。 */
    function renderAppToolbar(state, onRefresh, onOpen) {
      var busy = state.status === 'loading' || state.status === 'refreshing'
      var meta = state.url
        ? '本机服务 ' + state.url.replace(/^http:\/\//, '') + (state.localUser ? ' · 用户 ' + state.localUser : '')
        : '正在连接本机服务…'
      return h(
        'div',
        {
          style: {
            flex: '0 0 auto',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '8px 12px',
            borderBottom: '1px solid ' + COLOR.line,
            minWidth: 0,
          },
        },
        h(
          'div',
          { style: { flex: '1 1 auto', minWidth: 0, display: 'flex', alignItems: 'baseline', gap: 8 } },
          h('div', { style: { fontSize: 13, fontWeight: 600, whiteSpace: 'nowrap' } }, APP_PANEL_LABEL),
          h('div', { style: mutedTextStyle({ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }) }, meta),
        ),
        h(
          'button',
          {
            type: 'button',
            title: '重新连接本机面板并刷新',
            onClick: busy ? undefined : onRefresh,
            disabled: busy,
            style: buttonStyle(busy),
          },
          busy ? '连接中…' : '刷新',
        ),
        h(
          'button',
          {
            type: 'button',
            title: '在系统浏览器里打开完整面板',
            onClick: state.url ? onOpen : undefined,
            disabled: !state.url,
            style: buttonStyle(!state.url),
          },
          '在浏览器打开',
        ),
      )
    }

    function renderAppPanel(state, onRefresh, onOpen) {
      var children = [renderAppToolbar(state, onRefresh, onOpen)]
      // 刷新失败但旧 iframe 还能用：只在顶部提示一条，不把已有界面拆掉
      if (state.error && state.url) {
        children.push(
          h(
            'div',
            {
              role: 'status',
              style: {
                flex: '0 0 auto',
                padding: '5px 12px',
                fontSize: 11,
                lineHeight: 1.5,
                wordBreak: 'break-word',
                background: COLOR.errorBg,
                borderBottom: '1px solid ' + COLOR.errorBorder,
              },
            },
            '刷新失败：' + String(state.error.message || state.error),
          ),
        )
      }

      var body
      if (state.url) {
        // key 带 rev：点「刷新」时强制重挂 iframe，等于重新加载完整应用
        body = h('iframe', {
          key: APP_PANEL_ID + '-frame-' + state.rev,
          src: state.url + '/',
          title: APP_PANEL_LABEL,
          allow: 'clipboard-read; clipboard-write',
          referrerPolicy: 'no-referrer',
          style: { flex: '1 1 auto', width: '100%', height: '100%', border: 0, display: 'block', minHeight: 0, background: 'transparent' },
        })
      } else if (state.error) {
        body = h('div', { style: { flex: '1 1 auto', overflowY: 'auto', padding: '14px 16px 20px' } }, renderAppError(state.error))
      } else {
        body = h(
          'div',
          { style: mutedTextStyle({ flex: '1 1 auto', padding: '18px 16px' }) },
          '正在启动本机完整面板（首次启动要几秒，之后秒开）…',
        )
      }
      children.push(body)

      return h(
        'div',
        {
          role: 'region',
          'aria-label': APP_PANEL_LABEL,
          style: {
            height: '100%',
            width: '100%',
            display: 'flex',
            flexDirection: 'column',
            boxSizing: 'border-box',
            overflow: 'hidden',
            color: 'inherit',
            fontFamily: 'inherit',
            fontSize: 13,
          },
        },
        children,
      )
    }

    /**
     * 完整应用面板：GET /ai-tutor/panel 拿本机 URL（宿主懒启动 Python 服务），
     * 然后用 iframe 铺满面板。失败只在面板内提示，不抛给宿主。
     */
    function AppPanel() {
      var stateTuple = React.useState({ status: 'loading', url: '', localUser: 0, error: null, rev: 0 })
      var state = stateTuple[0]
      var setState = stateTuple[1]
      var mountedRef = React.useRef(true)

      var load = React.useCallback(function () {
        if (!mountedRef.current) return
        if (typeof fetch !== 'function') {
          setState(function (prev) {
            return Object.assign({}, prev, {
              status: prev.url ? 'ready' : 'error',
              error: { message: '宿主浏览器不支持 fetch，完整面板无法打开。', hint: '', status: 0 },
            })
          })
          return
        }
        var controller = null
        try {
          if (typeof AbortController === 'function') controller = new AbortController()
        } catch (error) {
          controller = null
        }
        var timer = controller ? setTimeout(function () {
          try { controller.abort() } catch (error) { /* 已结束 */ }
        }, APP_REQUEST_TIMEOUT_MS) : null
        var finish = function () { if (timer) { clearTimeout(timer); timer = null } }

        setState(function (prev) {
          return Object.assign({}, prev, { status: prev.url ? 'refreshing' : 'loading', error: null })
        })

        fetch(APP_ENDPOINT, {
          method: 'GET',
          headers: { accept: 'application/json' },
          cache: 'no-store',
          signal: controller ? controller.signal : undefined,
        })
          .then(function (response) {
            return response.text().then(function (text) {
              var payload = null
              try { payload = text ? JSON.parse(text) : null } catch (error) { payload = null }
              if (!response.ok) {
                var detail = payload && typeof payload === 'object'
                  ? String(payload.error || payload.detail || payload.message || ('HTTP ' + response.status))
                  : 'HTTP ' + response.status
                var failure = new Error(detail)
                failure.status = response.status
                failure.hint = payload && typeof payload === 'object' && payload.hint ? String(payload.hint) : ''
                throw failure
              }
              return payload
            })
          })
          .then(function (payload) {
            finish()
            if (!mountedRef.current) return
            var url = normalizePanelUrl(payload && payload.url)
            if (!url) {
              var invalid = new Error('宿主返回的面板地址不合法：' + String((payload && payload.url) || '空'))
              invalid.status = 0
              throw invalid
            }
            setState(function (prev) {
              return {
                status: 'ready',
                url: url,
                localUser: toNumber(payload && payload.localUser) || 0,
                error: null,
                rev: prev.rev + 1,
              }
            })
          })
          .catch(function (error) {
            finish()
            if (!mountedRef.current) return
            var aborted = error && error.name === 'AbortError'
            setState(function (prev) {
              return Object.assign({}, prev, {
                status: prev.url ? 'ready' : 'error',
                error: {
                  message: aborted
                    ? '请求超时（超过 ' + Math.round(APP_REQUEST_TIMEOUT_MS / 1000) + ' 秒没有响应；首次启动可能要更久）'
                    : (error && error.message ? error.message : String(error)),
                  hint: error && error.hint ? error.hint : '',
                  status: error && error.status ? error.status : 0,
                },
              })
            })
          })
      }, [])

      React.useEffect(function () {
        mountedRef.current = true
        load()
        return function () { mountedRef.current = false }
      }, [load])

      var openInBrowser = React.useCallback(function () {
        if (!state.url) return
        try {
          if (typeof window !== 'undefined' && typeof window.open === 'function') {
            window.open(state.url + '/', '_blank', 'noopener,noreferrer')
          }
        } catch (error) {
          console.warn('[ai-tutor] 打开系统浏览器失败：', error)
        }
      }, [state.url])

      return renderAppPanel(state, load, openInBrowser)
    }

    // --------------------------------------------------------------- 注册

    /**
     * 客户端插件入口。任一 slot 注册失败都只 console.error，不影响另一个，
     * 更不影响宿主（apply 整体包在 try/catch 里）。
     */
    function apply(ctx) {
      try {
        if (!ctx || !ctx.slots || typeof ctx.slots.inject !== 'function' || typeof ctx.slots.register !== 'function') {
          console.warn('[ai-tutor] 宿主没有可用的 slots 服务，AI-tutor 面板未注册（模型侧工具不受影响）')
          return
        }
        var disposers = []

        function own(result) {
          if (typeof result === 'function') disposers.push(result)
        }

        function registerSafely(label, run) {
          try {
            own(run())
          } catch (error) {
            console.error('[ai-tutor] 注册 ' + label + ' 失败：', error)
          }
        }

        // 1) 面板体：main 是 keyed slot，key 必须等于侧栏图标的 id
        registerSafely('main 面板', function () {
          return ctx.slots.inject('main', function () {
            return ctx.slots.register({ name: 'main', key: PANEL_ID }, function GraphPanelView() {
              return h(PanelErrorBoundary, null, h(GraphPanel))
            })
          })
        })

        // 2) 侧栏图标：list slot，owner props = { size, active }
        registerSafely('sidebar.panellist 图标', function () {
          return ctx.slots.inject('sidebar.panellist', function () {
            return ctx.slots.register(
              {
                name: 'sidebar.panellist',
                id: PANEL_ID,
                order: PANEL_ORDER,
                label: function () { return PANEL_LABEL },
              },
              function GraphIconSlot(props) {
                return h(GraphIcon, { size: props && typeof props.size === 'number' ? props.size : undefined, active: Boolean(props && props.active) })
              },
            )
          })
        })

        // 3) 完整应用的面板体：key 与下面的侧栏图标 id 一致
        registerSafely('main 完整面板', function () {
          return ctx.slots.inject('main', function () {
            return ctx.slots.register({ name: 'main', key: APP_PANEL_ID }, function AppPanelView() {
              // 完整面板的渲染错误用 renderAppError（比图谱面板多一条「在浏览器打开」的兜底建议）
              return h(PanelErrorBoundary, { renderError: renderAppError }, h(AppPanel))
            })
          })
        })

        // 4) 完整应用的侧栏图标
        registerSafely('sidebar.panellist 完整面板图标', function () {
          return ctx.slots.inject('sidebar.panellist', function () {
            return ctx.slots.register(
              {
                name: 'sidebar.panellist',
                id: APP_PANEL_ID,
                order: APP_PANEL_ORDER,
                label: function () { return APP_PANEL_LABEL },
              },
              function AppIconSlot(props) {
                return h(AppIcon, { size: props && typeof props.size === 'number' ? props.size : undefined, active: Boolean(props && props.active) })
              },
            )
          })
        })

        // 宿主卸载插件时回收这四处注册（ctx.effect 的执行函数返回 disposer）
        if (typeof ctx.effect === 'function' && disposers.length > 0) {
          try {
            ctx.effect(function () {
              return function () {
                while (disposers.length > 0) {
                  var dispose = disposers.pop()
                  if (typeof dispose === 'function') {
                    try { dispose() } catch (error) { console.error('[ai-tutor] 面板注册清理失败：', error) }
                  }
                }
              }
            })
          } catch (error) {
            console.error('[ai-tutor] 面板清理注册失败（不影响面板本身）：', error)
          }
        }
      } catch (error) {
        console.error('[ai-tutor] 客户端插件初始化失败：', error)
      }
    }

    module.exports = {
      name: 'dsh-ai-tutor',
      inject: ['slots'],
      apply: apply,
    }

    return module.exports
  },
})
