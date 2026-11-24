import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { chromium } from 'playwright'

const origin = process.env.AI_COLLAB_DESKTOP_TEST_URL || 'http://127.0.0.1:5188'
const manifest = JSON.parse(readFileSync('../var/studychat-five-pilot.json', 'utf8').replace(/^\uFEFF/, ''))
const browser = await chromium.launch({ channel: process.env.AI_COLLAB_BROWSER || 'msedge', timeout: 15000 })
const page = await browser.newPage({ viewport: { width: 1500, height: 980 } })
page.setDefaultTimeout(12000)
const errors = []
page.on('pageerror', error => errors.push(error.message))

async function get(path) {
  const response = await page.request.get(origin + '/api' + path)
  assert.equal(response.status(), 200, path)
  return response.json()
}

try {
  await page.goto(origin + '/#/login/teacher')
  await page.getByRole('button', { name: '以教师身份登录', exact: true }).click()
  await page.getByRole('heading', { name: '数据集评价', exact: true }).waitFor()
  for (const item of manifest.cases) {
    const sid = item.session_id
    const job = await get(`/jobs/${item.job_id}`)
    assert.equal(job.status, 'completed')
    assert.equal(job.succeeded_count, manifest.cases.filter(candidate => candidate.job_id === item.job_id).length)
    const currentRun = job.runs.find(run => run.id === item.run_id)
    assert.equal(currentRun?.report_id, item.report_id)
    assert.equal(currentRun?.actual_model_calls, item.actual_model_calls)
    const info = await get(`/sessions/${sid}`)
    const entry = info.reports.find(report => report.id === item.report_id)
    const historicalEntry = info.reports.find(report => report.id === item.historical_report_id)
    assert.ok(entry && historicalEntry, `missing current or historical model report for ${sid}`)
    const historical = await get(`/evaluation-runs/${historicalEntry.run_id}?report_id=${historicalEntry.id}`)
    assert.equal(historical.rubric_version, 'bars-2.0-five-dimension')
    const report = await get(`/evaluation-runs/${item.run_id}?report_id=${item.report_id}`)
    assert.equal(report.rubric_version, 'bars-3.0-five-point')
    assert.equal(report.degraded, false)
    assert.equal(report.dimensions.length, 5)
    const scored = report.dimensions.filter(part => part.state === 'scored')
    assert.ok(scored.every(part => part.level >= 1 && part.level <= 5))
    const expectedFive = scored.length < 3 ? null : Math.round(scored.reduce((sum, part) => sum + part.level, 0) / scored.length * 100) / 100
    const expectedPercent = expectedFive == null ? null : Math.round(expectedFive / 5 * 1000) / 10
    assert.equal(report.process_score_five, expectedFive)
    assert.equal(report.process_score, expectedPercent)
    assert.equal(report.provider_call.dialogue_messages, report.studychat_quality.scoped_event_ids.length - report.out_of_task_event_ids
      .filter(id => report.studychat_quality.scoped_event_ids.includes(id)).length)
    assert.ok(report.provider_call.actual_model_calls >= 2 && report.provider_call.provider_response_valid === true)
    assert.equal(report.outcome.score, null)
    assert.equal(report.system_outcome_score, null)
    assert.equal(report.outcome_basis, 'source_grade')
    assert.equal(report.source_outcome_score, info.external_scores.assignment_normalized)
    assert.equal(report.source_outcome_percent, Math.round(info.external_scores.assignment_normalized * 10000) / 100)
    assert.ok(report.diagnosis.label)
    assert.ok(report.teacher_recommendations.length >= 3 && report.student_recommendations.length >= 3)
    await page.goto(`${origin}/#/session/${sid}/${item.run_id}/${item.report_id}`)
    await page.getByRole('heading', { name: info.studychat.assignment_title, exact: true }).waitFor()
    await page.getByRole('heading', { name: '五维过程评价', exact: true }).waitFor()
    await page.getByRole('heading', { name: '教师复核', exact: true }).waitFor()
    assert.ok(!info.studychat.assignment_title.includes('*'))
    assert.equal(await page.locator('.study-question-text [data-task-text]').getAttribute('data-task-text'), info.task_instructions)
    assert.equal(await page.locator('.study-dimension').count(), 5)
    assert.equal(await page.locator('.study-dimension').getByText(/\/ 5 · 已评分/).count(), scored.length)
    const colors = await page.locator('.study-dimension').evaluateAll(cards => cards.map(card => getComputedStyle(card).borderLeftColor))
    assert.equal(new Set(colors).size, 5)
    assert.equal(await page.locator('#study-performance').getByText('来源成果分', { exact: true }).count(), 1)
    assert.equal(await page.locator('#study-performance').getByText('系统成果分', { exact: true }).count(), 0)
    await page.getByText('StudyChat 原始作业成绩 · 百分制', { exact: true }).waitFor()
    await page.locator('#study-data').getByText('本次未对归档提交物重新执行成果量规。', { exact: true }).waitFor()
    await page.getByText('本地成果评价', { exact: true }).click()
    const materials = page.getByLabel('查看提交材料')
    await materials.locator('option').nth(1).waitFor({ state: 'attached' })
    assert.equal(await materials.locator('option').count(), info.studychat.submission_count + 1)
    await materials.selectOption({ index: 1 })
    await page.locator('.local-outcome-preview').waitFor()
    await page.getByText('本地成果评价', { exact: true }).click()
    const sectionIds = await page.locator('.study-report-main > .study-section').evaluateAll(nodes => nodes.map(el => el.id))
    assert.deepEqual(sectionIds, ['study-question','studychat-dialogue','study-evaluation','study-performance','study-guidance','study-data'])
    await page.getByRole('heading', { name: '给教师的教学建议', exact: true }).waitFor()
    await page.getByRole('heading', { name: '给学生的改进建议', exact: true }).waitFor()
    assert.ok(report.teacher_recommendations.every(item => item.observation && item.judgment && item.action && item.follow_up && item.event_ids.length))
    assert.ok(report.student_recommendations.every(item => item.action.startsWith('你') && !/[?？]/.test(item.action)))
    assert.ok(report.transfer_tasks.length >= 1 && report.transfer_tasks.length <= 2)
    const rendered = new Set()
    let rightNotes = 0
    for (let index = 1; index <= Math.ceil(item.chat_messages / 16); index++) {
      const data = await get(`/sessions/${sid}/dialogue?page=${index}&page_size=16`)
      assert.equal(data.total, item.chat_messages)
      const first = page.locator(`#turn-${data.items[0].id}`)
      await first.waitFor()
      if (index === 1 && process.env.AI_COLLAB_CAPTURE === '1') {
        await page.evaluate(() => window.scrollTo(0, 0))
        await page.screenshot({ path: `../var/studychat-${item.term}-${item.assignment}.png`, animations: 'disabled' })
      }
      const display = await page.locator('[data-event-id]').evaluateAll(elements => elements.map(event => ({
        id: event.dataset.eventId, turn: Number(event.dataset.turn), text: event.querySelector('[data-full-message]').textContent,
      })))
      assert.deepEqual(display, data.items.map(event => ({ id: event.id, turn: event.turn, text: event.text || '（空消息）' })))
      rightNotes += await page.locator('.study-round-notes:not(.is-empty)').evaluateAll(notes => notes.filter(note => {
        const prior = note.parentElement.querySelector('.study-round-messages')
        return note.getBoundingClientRect().left > prior.getBoundingClientRect().left &&
          [...note.querySelectorAll('[data-anchor-event]')].every(item => prior.querySelector(`[data-event-id="${item.dataset.anchorEvent}"]`))
      }).length)
      for (const event of display) rendered.add(event.id)
      const next = page.getByRole('button', { name: '下一页', exact: true }).first()
      if (index < Math.ceil(item.chat_messages / 16)) await next.click()
    }
    assert.equal(rendered.size, item.chat_messages)
    assert.ok(rightNotes > 0)
    const evidence = [...report.dimensions.flatMap(part => part.event_ids),
      ...report.annotations.flatMap(part => part.evidence_refs.map(ref => ref.event_id)),
      ...report.ai_behaviors.flatMap(part => part.event_ids),
      ...report.teaching_recommendations.flatMap(part => part.event_ids), ...report.teaching_diagnosis.event_ids,
      ...report.teacher_recommendations.flatMap(part => part.event_ids), ...report.student_recommendations.flatMap(part => part.event_ids),
      ...report.transfer_tasks.flatMap(part => part.event_ids)]
    assert.ok(evidence.length > 0 && evidence.every(id => rendered.has(id)))
    assert.ok(evidence.every(id => !report.studychat_quality.excluded_event_ids.includes(id)))
    if (item.out_of_task_event_sequences) {
      assert.equal(report.out_of_task_event_ids.length, item.out_of_task_event_sequences.length)
      assert.ok(report.out_of_task_event_ids.every(id => rendered.has(id) && !evidence.includes(id)))
      await page.getByText('与该作业题目不匹配 · 不参与评分', { exact: true }).first().waitFor()
    }
    const dimension = report.dimensions.find(part => part.event_ids.length > 0)
    const evidenceButton = page.locator('.study-dimension').nth(report.dimensions.indexOf(dimension)).getByRole('button', { name: '证据 1', exact: true })
    await evidenceButton.click()
    await page.locator(`#turn-${dimension.event_ids[0]}.study-target`).waitFor()
    const collapsed = page.locator('.study-long:not([open])').first()
    if (await collapsed.count()) {
      const summary = collapsed.locator('summary')
      await summary.click()
      await page.locator('.study-long[open] [data-full-message]').first().waitFor()
    }
    const annotationButton = page.locator('.study-note').getByRole('button', { name: '证据 1', exact: true }).first()
    if (await annotationButton.count()) await annotationButton.click()
    if (process.env.AI_COLLAB_CAPTURE === '1') {
      await page.locator('.study-round-notes:not(.is-empty)').first().scrollIntoViewIfNeeded()
      await page.screenshot({ path: `../var/studychat-${item.term}-${item.assignment}-dialogue.png`, animations: 'disabled' })
      await page.locator('#study-performance').screenshot({ path: `../var/studychat-${item.term}-${item.assignment}-performance.png` })
      await page.locator('#study-guidance').screenshot({ path: `../var/studychat-${item.term}-${item.assignment}-guidance.png` })
    }
    await page.setViewportSize({ width: 390, height: 850 })
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'mobile horizontal overflow')
    assert.ok(await page.locator('.study-round-notes:not(.is-empty)').first().evaluate(note =>
      note.getBoundingClientRect().top >= note.parentElement.querySelector('.study-round-messages').getBoundingClientRect().bottom))
    await page.setViewportSize({ width: 1500, height: 980 })
    console.log(`PASS ${item.term} ${item.assignment} ${item.turns} turns, ${rendered.size} messages, ${report.annotations.length} annotations, ${new Set(evidence).size} valid evidence events`)
  }
  assert.deepEqual(errors, [])
} finally {
  await browser.close()
}
