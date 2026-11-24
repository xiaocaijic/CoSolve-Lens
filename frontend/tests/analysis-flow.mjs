import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { chromium } from 'playwright'

const origin = process.env.AI_COLLAB_DESKTOP_TEST_URL || 'http://127.0.0.1:5188'
const manifest = JSON.parse(readFileSync('../var/studychat-five-pilot.json', 'utf8').replace(/^\uFEFF/, ''))
const mode = process.env.AI_COLLAB_QUALITY_MODE || 'scan'
const caseName = process.env.AI_COLLAB_QUALITY_CASE || 'S25 A7'
const tag = process.env.AI_COLLAB_QUALITY_TAG || 'quality'
const negative = { session_id: '17cde0db988d383fc58619386200e823', term: 'F24', assignment: 'A1',
  chat_id: 'bfc8ac14-a8a9-4313-816f-561e48d87c44' }
const shots = process.env.AI_COLLAB_CAPTURE === '1'
const browser = await chromium.launch({ channel: process.env.AI_COLLAB_BROWSER || 'msedge', timeout: 15000 })
async function freshPage() {
  const result = await browser.newPage({ viewport: { width: 1470, height: 950 } })
  result.setDefaultTimeout(15000)
  return result
}
let page = await freshPage()
const button = name => page.getByRole('button', { name, exact: true })
const heading = name => page.getByRole('heading', { name, exact: true })
const shot = async file => { if (shots) await page.screenshot({ path: `../var/studychat-${tag}-${file}.png` }) }

async function scan(target, role) {
  await page.goto(origin + '/#/login/' + role)
  await button(role === 'admin' ? '以管理员身份登录' : '以教师身份登录').click()
  if (role === 'admin') await page.getByRole('link', { name: '数据集评价', exact: true }).click()
  await button('现场分析').click()
  await heading('StudyChat v1.1 · 内置数据集').waitFor()
  const response = await page.request.get(`${origin}/api/datasets/${manifest.source_id}/analysis-options`)
  assert.equal(response.status(), 200)
  const entry = (await response.json()).items.find(item => item.id === target.session_id)
  assert.ok(entry, 'dataset contains target')
  await button('下一步 · 选择范围').click()
  await page.getByLabel('学期', { exact: true }).selectOption(target.term)
  await page.getByLabel('作业', { exact: true }).selectOption(target.assignment)
  await page.getByLabel('最多分析数量', { exact: true }).selectOption('1')
  await page.getByLabel('搜索会话').fill(target.chat_id || entry.id)
  await page.getByRole('checkbox', { name: `选择会话 ${entry.title} ${entry.participant}`, exact: true }).check()
  await button('检查数据质量').click()
  await heading('数据质量检查').waitFor()
  await page.getByRole('list', { name: '数据缺失情况' }).waitFor()
}

const start = () => button('使用 DeepSeek 生成五维评价')
try {
  if (mode !== 'live') {
    await scan(negative, 'teacher')
    await page.getByText('最终可评价 0', { exact: true }).waitFor()
    await page.getByText('实际 2 轮 · 声明 3 轮 · 与作业无关', { exact: true }).waitFor()
    assert.equal(await start().isDisabled(), true)
    await shot('negative-scan')
    await page.getByRole('link', { name: '查看原始会话' }).click()
    await heading('StudyChat 被排除记录').waitFor()
    await heading('完整人机交互过程').waitFor()
    await page.getByText('实际 2 轮 · 声明 3 轮', { exact: true }).waitFor()
    await page.getByText('档案有作业成绩。', { exact: false }).waitFor()
    assert.equal(await heading('Future of Work').count(), 0)
    assert.equal(await page.getByText('材料和交付要求', { exact: true }).count(), 0)
    for (const title of ['五维过程评价', '综合表现', '教学建议', '二维诊断']) assert.equal(await heading(title).count(), 0)
    assert.equal(await page.getByText('来源成果分', { exact: true }).count(), 0)
    await page.locator('[data-event-id]').nth(3).waitFor()
    assert.equal(await page.locator('[data-event-id]').count(), 4)
    await shot('negative')
    console.log('PASS incomplete and off-task negative case scan and excluded detail')
    await page.context().close()
    page = await freshPage()
    const target = manifest.cases.find(item => `${item.term} ${item.assignment}` === caseName)
    await scan(target, mode === 'setup' ? 'admin' : 'teacher')
    await page.getByText('最终可评价 1', { exact: true }).waitFor()
    await page.getByText(mode === 'setup' ? /请在管理员系统设置启用 DeepSeek Flash/ : /请从管理员入口启动/).waitFor()
    assert.equal(await start().isDisabled(), true)
    console.log('PASS blocked DeepSeek with actionable reason')
  } else {
    const target = manifest.cases.find(item => `${item.term} ${item.assignment}` === caseName)
    assert.ok(target)
    await scan(target, 'admin')
    await page.getByText('最终可评价 1', { exact: true }).waitFor()
    assert.equal(await start().isDisabled(), true)
    await page.getByRole('checkbox', { name: /确认调用已授权且通过检查/ }).check()
    const outgoing = page.waitForResponse(res => res.url().endsWith('/api/analysis/start') && res.request().method() === 'POST')
    await start().click()
    const queued = await outgoing
    assert.equal(queued.status(), 202, await queued.text())
    const first = await queued.json()
    assert.equal(first.runs.length, 1)
    await heading('现场分析进度').waitFor()
    await page.getByText(/DeepSeek 正在核对作业相关性|正在执行五维分析/).first().waitFor({ timeout: 250000 })
    await shot('progress')
    console.log('QUEUED ' + JSON.stringify({ job: first.id, run: first.runs[0].id, session: target.session_id }))
    await heading('本次新生成的报告').waitFor({ timeout: 250000 })
    const done = (await page.request.get(origin + '/api/jobs/' + first.id)).ok()
      ? await (await page.request.get(origin + '/api/jobs/' + first.id)).json() : null
    const run = done?.runs[0]
    assert.equal(done?.status, 'completed', JSON.stringify(done))
    assert.equal(run?.provider, 'deepseek')
    assert.equal(run?.provider_response_valid, 1)
    assert.ok(run.actual_model_calls >= 2 && run.segment_count >= 1)
    await page.getByText('真实返回已校验', { exact: false }).waitFor()
    const link = page.getByRole('link', { name: '查看本次报告', exact: true })
    assert.ok((await link.getAttribute('href')).includes(run.id))
    await link.click()
    await heading('五维过程评价').waitFor()
    assert.equal(await page.locator('.study-dimension').count(), 5)
    const report = await (await page.request.get(origin + '/api/evaluation-runs/' + run.id + '?report_id=' + run.report_id)).json()
    assert.equal(report.run_id, run.id)
    assert.equal(report.outcome_basis, 'source_grade')
    assert.equal(report.studychat_quality.task_relevance, 'relevant')
    assert.equal(report.studychat_quality.grade_link_valid, true)
    assert.ok(report.provider_call.stages.every(stage => stage.id))
    const evidence = page.locator('.study-dimension').getByRole('button', { name: /^证据/ }).first()
    await evidence.click()
    await page.locator('.study-target').waitFor()
    await page.locator('#study-performance').scrollIntoViewIfNeeded()
    await shot('positive')
    console.log('VERIFIED ' + JSON.stringify({ job: first.id, run: run.id, report: run.report_id, provider: run.provider,
      model: report.model, calls: run.actual_model_calls, segments: run.segment_count, process: report.process_score,
      grade: report.source_outcome_percent, url: page.url() }))
  }
} finally {
  await browser.close()
}
