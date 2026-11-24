import assert from 'node:assert/strict'
import { chromium } from 'playwright'

const origin = process.env.AI_COLLAB_DESKTOP_TEST_URL || 'http://127.0.0.1:5188'
const browser = await chromium.launch({ channel: process.env.AI_COLLAB_BROWSER || 'msedge' })
const viewport = { width: 1480, height: 930 }
const teacher = await browser.newPage({ viewport })
const admin = await browser.newPage({ viewport })
const startRequests = []
teacher.on('request', request => {
  if (request.url().includes('/analysis/start')) startRequests.push(request)
})
const button = (page, name) => page.getByRole('button', { name, exact: true })
const heading = (page, name) => page.getByRole('heading', { name, exact: true })
const shot = async name => {
  if (process.env.AI_COLLAB_CAPTURE === '1') await teacher.screenshot({ path: `../var/studychat-showcase-${name}.png` })
}
const formalTitles = {
  'slime-feature-check': 'F24 A4 · Equation of a Slime',
  'bayes-condition': 'F24 A6 · Bayes Complete',
  'search-order-explanation': 'S25 A2 · SearchComplete · 方案比较',
  'neural-test-data': 'S25 A7 · Neural Complete · 结果核验',
  'documentary-timestamp': 'F24 A1 · Future of Work',
  'trie-heap': 'F24 A2 · SearchComplete · 错误诊断',
  'bfs-expected-first': 'S25 A2 · SearchComplete · 结果核验',
  'ngram-done-claim': 'S25 A6 · Bayes Complete',
}
const historicalIds = new Set(['slime-feature-check', 'bayes-condition', 'search-order-explanation', 'neural-test-data'])

let settings, csrf
const settingsUrl = origin + '/api/settings/studychat-entries'
try {
  await teacher.goto(origin + '/#/login/teacher')
  await button(teacher, '以教师身份登录').click()
  await heading(teacher, 'StudyChat 精选案例').waitFor()
  const cards = teacher.locator('.showcase-card')
  await cards.nth(11).waitFor()
  assert.equal(await cards.count(), 12)
  await heading(teacher, 'StudyChat 精选案例').scrollIntoViewIfNeeded()
  await shot('library')

  const result = await teacher.request.get(origin + '/api/studychat/showcase')
  assert.equal(result.status(), 200)
  const { items } = await result.json()
  assert.equal(new Set(items.map(item => item.session_id)).size, 12)
  assert.equal(items.filter(item => item.report).length, 8)
  assert.equal(items.filter(item => item.historical_report).length, 4)
  for (const [id, title] of Object.entries(formalTitles)) {
    const item = items.find(candidate => candidate.id === id)
    assert.equal(item?.title, title)
    assert.equal(item?.report?.rubric_version, 'bars-3.0-five-point')
    assert.ok(item?.report?.process_score_five != null)
    if (historicalIds.has(id)) assert.equal(item?.historical_report?.rubric_version, 'bars-2.0-five-dimension')
    const reportResponse = await teacher.request.get(`${origin}/api/evaluation-runs/${item.report.run_id}?report_id=${item.report.id}`)
    assert.equal(reportResponse.status(), 200)
    const report = await reportResponse.json()
    assert.equal(report.prompt_version, 'prompt-4.1-calibrated-guidance')
    assert.ok(report.student_recommendations.every(note => note.action.startsWith('你') && !/[?？]/.test(note.action)))
    assert.ok(report.transfer_tasks.length >= 1 && report.transfer_tasks.length <= 2)
  }
  let referenceCount = 0
  for (const card of items) {
    const caseResponse = await teacher.request.get(origin + '/api/studychat/showcase/' + card.id)
    assert.equal(caseResponse.status(), 200)
    const example = await caseResponse.json()
    assert.ok(example.finding && example.exercise)
    assert.ok(example.evidence.length >= 3 && example.evidence.every(ref => ref.excerpt && ref.id))
    referenceCount += example.evidence.length
  }
  assert.equal(referenceCount, 39)

  await teacher.getByLabel('按主题查看').selectOption('report')
  assert.equal(await cards.count(), 8)
  for (const [id, title] of Object.entries(formalTitles)) {
    const item = items.find(candidate => candidate.id === id)
    await teacher.goto(`${origin}/#/showcase/${id}`)
    await heading(teacher, title).waitFor()
    await heading(teacher, '关键对话证据').waitFor()
    await teacher.getByText('预备内容 · 0 次模型调用', { exact: true }).waitFor()
    await button(teacher, '展开预备讲评').click()
    await teacher.getByText('已存档的五维报告', { exact: false }).waitFor()
    assert.equal(await teacher.getByText('历史折算', { exact: false }).count(), 0)
    await teacher.getByRole('link', { name: '打开当前默认正式报告', exact: true }).click()
    await heading(teacher, '五维过程评价').waitFor()
    assert.equal(await teacher.locator('.study-dimension').count(), 5)
    assert.ok((await teacher.locator('.study-dimension .badge').allTextContents()).every(text => /\/ 5/.test(text)))
    assert.equal(await teacher.locator('#study-performance strong').first().textContent(), `${item.report.process_score_five} / 5`)
    const evidence = teacher.locator('.study-dimension').getByRole('button', { name: '证据 1', exact: true }).first()
    await evidence.click()
    await teacher.locator('.study-target').waitFor()
    await heading(teacher, '迁移练习').waitFor()
    if (historicalIds.has(id)) {
      await teacher.goto(`${origin}/#/showcase/${id}`)
      await button(teacher, '展开预备讲评').click()
      await teacher.getByRole('link', { name: '查看历史三分制报告', exact: true }).click()
      await heading(teacher, '五维过程评价').waitFor()
      assert.ok((await teacher.locator('.study-dimension .badge').allTextContents()).every(text => /\/ 3/.test(text)))
    }
  }
  await teacher.goto(origin + '/#/datasets/replay')
  await heading(teacher, 'StudyChat 精选案例').waitFor()
  await shot('case')
  await teacher.locator('a[href="#/showcase/cleaning-id-overlap"]').first().click()
  await button(teacher, '展开预备讲评').click()
  await heading(teacher, '课堂练习').waitFor()
  assert.equal(await teacher.getByRole('link', { name: '打开当前默认正式报告' }).count(), 0)
  assert.equal(startRequests.length, 0)
  console.log('PASS 12 replay cases, 39 grounded references, 8 native five-point reports plus history, no analysis started')

  await teacher.getByRole('link', { name: '返回精选案例', exact: true }).click()
  await button(teacher, '现场分析').click()
  await heading(teacher, '选择数据源').waitFor()
  await button(teacher, '下一步 · 选择范围').click()
  await teacher.getByText(/找到 12 条/).waitFor()
  console.log('PASS live wizard opens with the same 12 curated choices')

  await admin.goto(origin + '/#/login/admin')
  await button(admin, '以管理员身份登录').click()
  await heading(admin, 'StudyChat 入口设置').waitFor()
  settings = await (await admin.request.get(origin + '/api/studychat/entry-settings')).json()
  csrf = (await (await admin.request.get(origin + '/api/auth/me')).json()).csrf
  await admin.getByLabel('开放现场分析', { exact: true }).uncheck()
  await button(admin, '保存入口设置').click()
  await admin.getByText('StudyChat 入口设置已保存。', { exact: true }).waitFor()
  await teacher.goto(origin + '/#/datasets/live')
  await teacher.reload()
  await heading(teacher, 'StudyChat 精选案例').waitFor()
  assert.equal(await button(teacher, '现场分析').count(), 0)
  console.log('PASS administrator saves the live gate and a direct visit respects it')
} finally {
  try {
    if (settings && csrf) {
      const restored = await admin.request.put(settingsUrl, { data: settings, headers: { 'X-CSRF-Token': csrf } })
      assert.equal(restored.status(), 200)
    }
  } finally { await browser.close() }
}
