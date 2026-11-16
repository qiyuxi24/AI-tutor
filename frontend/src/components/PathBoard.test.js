/**
 * 学习路径「依赖脊」渲染回归（DOM 级，补 pathRail.test.js 的纯函数用例）。
 *
 * 锁住用户可见的那条不变量：**没选中行时，主干旁边一条前置弧都不画**
 * （旧版无条件画全部前置 → 19px 的 gutter 里叠成一团"杂乱的线"）。
 */
import { describe, it, expect } from 'vitest'
import { createApp, h, nextTick } from 'vue'
import PathBoard from './PathBoard.vue'

const items = [
  { id: 'a', name: '质点', status: 'unstarted', level: 0, prerequisites: [], hint: '' },
  { id: 'b', name: '位移', status: 'learning', level: 1, prerequisites: [{ id: 'a' }], hint: '建议先补：质点' },
  { id: 'c', name: '速度', status: 'weak', level: 2, prerequisites: [{ id: 'a' }, { id: 'b' }], hint: '建议先补：质点、位移' },
]
const recommended = items.map(({ id, name, level }) => ({ id, name, level }))

function mount() {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp({
    render: () => h(PathBoard, { items, recommended, subject: '运动学', board: '' }),
  })
  app.mount(host)
  return { app, host }
}

const arcsOf = (host) => host.querySelectorAll('path.rail-arc')

describe('PathBoard 依赖脊', () => {
  it('默认（没选中任何行）不画任何前置弧', () => {
    const { app, host } = mount()
    expect(host.querySelectorAll('.pb-row')).toHaveLength(3)
    expect(arcsOf(host)).toHaveLength(0)
    app.unmount()
  })

  it('点选一行 → 只画这一行的前置弧；再点一次取消 → 弧消失', async () => {
    const { app, host } = mount()
    const row = host.querySelector('[data-node="c"]')
    row.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await nextTick()
    expect(arcsOf(host)).toHaveLength(2) // 速度 的两个前置（质点 / 位移）

    row.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await nextTick()
    expect(arcsOf(host)).toHaveLength(0)
    app.unmount()
  })

  it('选中的行没有前置（起点）→ 也不画弧', async () => {
    const { app, host } = mount()
    host.querySelector('[data-node="a"]').dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await nextTick()
    expect(arcsOf(host)).toHaveLength(0)
    app.unmount()
  })
})
