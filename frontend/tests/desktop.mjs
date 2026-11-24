import assert from 'node:assert/strict'
import { chromium } from 'playwright'

const origin = process.env.AI_COLLAB_DESKTOP_TEST_URL || 'http://127.0.0.1:5188'
const browser = await chromium.launch({ channel: process.env.AI_COLLAB_BROWSER || 'msedge', timeout: 15000 })
const cases = [
  { role: 'teacher', name: '教师', destination: '数据集评价' },
  { role: 'student', name: '学生', destination: '真人任务' },
  { role: 'admin', name: '管理员', destination: '系统设置' },
]
const windows = []
try {
  for (const { role, name, destination } of cases) {
    // Each executable opens a separate browser profile. Separate contexts
    // exercise the corresponding cookie isolation without visible windows.
    const context = await browser.newContext()
    const page = await context.newPage()
    page.setDefaultTimeout(10000)
    if (role === 'admin') {
      await page.route('**/api/settings/llm', route => route.fulfill({ json: {
        provider: 'deepseek', model: 'deepseek-flash', enabled: false, timeout: 45,
        retention_confirmed: false, usage_note: '', deepseek_key_configured: true,
      } }))
      await page.route('**/api/settings/deepseek-test', route => {
        const { model } = route.request().postDataJSON()
        assert.equal(model, 'deepseek-v4-pro')
        return route.fulfill({ json: { model, message: '模型自检通过' } })
      })
    }
    await page.goto(origin + '/#/login/' + role)
    await page.getByRole('heading', { name: name + '登录', exact: true }).waitFor()
    assert.equal(await page.title(), name + ' · CoSolve Lens')
    const choices = page.getByRole('button', { name: /^以.*身份登录$/ })
    assert.equal(await choices.count(), 1)
    await page.getByRole('button', { name: '以' + name + '身份登录', exact: true }).click()
    await page.getByRole('heading', { name: destination, exact: true }).waitFor()
    if (role === 'teacher') {
      await page.getByRole('button', { name: '现场分析', exact: true }).click()
      await page.getByRole('heading', { name: 'StudyChat v1.1 · 内置数据集' }).waitFor()
      await page.getByText('987 条会话 · 16851 条历史交互').waitFor()
    }
    if (role === 'student') {
      await page.getByRole('heading', { name: '学生任务库' }).waitFor()
      await page.locator('.task-library-card').nth(3).waitFor()
      assert.equal(await page.locator('.task-library-card').count(), 10)
      if (process.env.AI_COLLAB_CAPTURE === '1') await page.screenshot({ path: '../var/student-task-library.png' })
    }
    if (role === 'admin') {
      assert.equal(await page.getByLabel('DeepSeek API Key').getAttribute('type'), 'password')
      await page.getByRole('button', { name: '保存 DeepSeek Key' }).waitFor()
      await page.getByLabel('模型名称').selectOption('deepseek-v4-pro')
      await page.getByLabel('外发用途与保留策略').fill('保留未保存的用途说明')
      await page.getByRole('button', { name: '连接测试（仅合成数据）' }).click()
      await page.getByText('deepseek-v4-pro · 模型自检通过').waitFor()
      assert.equal(await page.getByLabel('外发用途与保留策略').inputValue(), '保留未保存的用途说明')
    }
    windows.push({ role, name, page })
  }
  for (const { role, name, page } of windows) {
    const response = await page.request.get(origin + '/api/auth/me')
    assert.equal(response.status(), 200)
    assert.equal((await response.json()).role, role)
    const shutdown = await page.request.post(origin + '/api/desktop-shutdown')
    assert.equal(shutdown.status(), 403)
    await page.getByRole('button', { name: '退出', exact: true }).click()
    await page.getByRole('heading', { name: name + '登录', exact: true }).waitFor()
    console.log('PASS ' + name + '独立登录入口、角色页面与退出路径')
  }
} finally {
  await browser.close()
}
