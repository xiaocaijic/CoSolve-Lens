import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { api, message } from './api'
import type { Annotation, DialogueMessage, EventDetail, Page, Report, SessionDetail } from './api'
import { qualityNames } from './analysisFlow'
import { Badge, Busy, Empty, ErrorNotice, Pager, dimensionNames, eventNames, fivePointRubric, methodNames, outcomeName, processDisplay, statusNames, useData } from './ui'

export type ReportReview = (report: Report, locate: (id: string) => void, refresh: () => void) => ReactNode
export type ReportSelection = { runId: string, reportId: string }
const pageSize = 16
const annotationNames: Record<string, string> = {
  positive: '正面', issue: '问题', turning_point: '转折', insufficient_evidence: '证据不足',
  rule_llm_conflict: '规则与模型冲突', record_conflict: '记录冲突', ai_context_issue: 'AI 内容核对',
}
const behaviorNames: Record<string, string> = { adopt: '采用', modify: '修改', question: '质疑', reject: '拒绝', verify: '验证', reask: '重新提问' }
const modelNames: Record<string, string> = { 'deepseek-flash': 'DeepSeek V4.1 Flash 1017' }
const sections = [['study-question', '任务问题'], ['studychat-dialogue', '交互过程'], ['study-evaluation', '过程评价'],
  ['study-performance', '综合表现'], ['study-guidance', '教学建议'], ['study-data', '数据与复核']]

function EvidenceLinks({ ids, locate, turns }: { ids: string[], locate: (id: string) => void, turns?: Report['evidence_turns'] }) {
  return <div className="evidence-links">{[...new Set(ids)].map((id, i) =>
    <button type="button" className="link-button" onClick={() => locate(id)} key={id}>{turns?.[id] ?
      `第 ${turns[id].turn} 轮 · E${String(turns[id].sequence).padStart(4,'0')}` : `证据 ${i + 1}`}</button>)}</div>
}

function taskRequirements(text: string) {
  return text.split(/(?=^#{1,6}\s)/m).filter(part => /(?:material|dataset|starter code|submit|submission|rubric|交付|材料)/i.test(part.split('\n')[0])).join('\n')
}

function TaskText({ text }: { text: string }) {
  return <div className="study-task-body" tabIndex={0} data-task-text={text}>{text.split(/(?=^#{1,6}\s)/m).map((section, index) => {
    const [title, ...body] = section.split('\n')
    if (!title.startsWith('#')) return <div key={index}>{section}</div>
    if (index === 0 && title.startsWith('# ')) return <div key={index}>{body.join('\n').trim()}</div>
    return <div key={index}><h3>{title.replace(/^#+\s*/, '').replace(/^\*+|\*+$/g, '')}</h3>{body.join('\n').trim()}</div>
  })}</div>
}

export function StudyChatView({ session, review, refresh, initial }: {
  session: SessionDetail, review?: ReportReview, refresh: () => void, initial?: ReportSelection,
}) {
  const [selection, setSelection] = useState<ReportSelection | undefined>(initial)
  const [page, setPage] = useState(1)
  const [target, setTarget] = useState('')
  const [attachment, setAttachment] = useState<EventDetail>()
  const [error, setError] = useState('')
  const facts = session.studychat!
  const quality = facts.quality
  const excluded = quality.eligibility_status !== 'eligible'
  const latest = session.reports.find(item => item.provider === 'deepseek' && !item.legacy_rubric)
    ?? session.reports.find(item => !item.legacy_rubric)
  const chosen = excluded ? undefined : selection ?? (latest && { runId: latest.run_id, reportId: latest.id })
  const reportData = useData<Report>(chosen ? `/evaluation-runs/${chosen.runId}?report_id=${chosen.reportId}` : null)
  const report = reportData.data?.session_id === session.id ? reportData.data : undefined
  const chatPath = `/sessions/${session.id}/dialogue?page_size=${pageSize}`
  const conversation = useData<Page<DialogueMessage>>(`${chatPath}&page=${page}`)
  const rounds = new Map<number, DialogueMessage[]>()
  for (const item of conversation.data?.items ?? []) rounds.set(item.turn, [...(rounds.get(item.turn) ?? []), item])
  const annotations = new Map<string, Annotation[]>()
  for (const item of report?.annotations ?? []) {
    const first = item.evidence_refs[0]?.event_id
    if (first) annotations.set(first, [...(annotations.get(first) ?? []), item])
  }
  const coverage = report?.process_coverage
  const sourceScore = quality.grade_link_valid ? report?.source_outcome_percent : undefined
  const systemScore = report?.system_outcome_score
  const localOutcome = report?.outcome.basis === 'local_review'
  const localDetails = report?.local_outcome ?? (localOutcome ? report?.outcome : undefined)
  const nativeFive = !!report && fivePointRubric(report.rubric_version)
  const requirements = taskRequirements(session.task_instructions)
  const anchors = new Map<string, string>()
  for (const round of rounds.values()) for (const item of round) anchors.set(item.id, `E${String(item.sequence_no).padStart(4, '0')}`)

  useEffect(() => {
    if (!target || !conversation.data) return
    const event = document.getElementById(`turn-${target}`)
    if (!event) return
    const detail = event.querySelector('details')
    if (detail) detail.open = true
    event.scrollIntoView({ block: 'center', behavior: 'smooth' })
    event.focus({ preventScroll: true })
  }, [target, conversation.data])

  function jump(id: string) { document.getElementById(id)?.scrollIntoView({ block: 'start', behavior: 'smooth' }) }

  async function locate(id: string) {
    setError(''); setAttachment(undefined)
    if (anchors.has(id)) {
      setTarget(id)
      if (target === id) document.getElementById(`turn-${id}`)?.scrollIntoView({ block: 'center', behavior: 'smooth' })
      return
    }
    try {
      const data = await api<Page<DialogueMessage>>(chatPath + '&event_id=' + encodeURIComponent(id))
      setTarget(id); setPage(data.page)
    } catch {
      try {
        setAttachment(await api<EventDetail>(`/sessions/${session.id}/events/${id}`))
        requestAnimationFrame(() => jump('studychat-other-evidence'))
      } catch (e) { setError(message(e)) }
    }
  }

  const pages = conversation.data && <Pager page={page} total={conversation.data.total} size={pageSize} set={next => {
    setTarget(''); setPage(next); jump('studychat-dialogue')
  }} />

  return <div className="studychat study-report-layout">
    <div className="stack study-report-main">
      <header className="study-hero study-section" id="study-question">
        <div className="study-overline">StudyChat · {excluded ? '原标作业 ' : ''}{facts.term} / {facts.assignment}</div>
        <h1>{excluded ? 'StudyChat 被排除记录' : facts.assignment_title}</h1>
        {excluded ? <div className="stack" aria-label="数据质量结论">
          <div className="chips">{quality.exclusion_reasons.map(reason => <Badge tone="warn" key={reason}>{qualityNames[reason]}</Badge>)}</div>
          <p>实际 {quality.observed_turns} 轮 · 声明 {quality.declared_turns ?? '未知'} 轮</p>
          <p><strong>{qualityNames[quality.task_relevance]}</strong> · {quality.relevance_reason}</p>
          <EvidenceLinks ids={quality.relevance_evidence_event_ids} locate={locate} />
          {quality.grade_matched && <p>档案有作业成绩。本段对话的记录质量或题目关联不足，成绩不纳入评价。</p>}
        </div> : <>
        <p className="study-meta">匿名学生 {session.participant ?? '未匹配'} <span>·</span> {facts.turn_count} 轮对话</p>
        <details open className="study-question-text"><summary>完整任务说明</summary>
          {session.task_instructions ? <TaskText text={session.task_instructions} /> : <p className="muted">该会话未关联题目全文。</p>}
        </details>
        <details className="study-requirements"><summary>材料和交付要求</summary>
          {requirements ? <TaskText text={requirements} /> : <p className="muted">材料和交付要求见原题。</p>}
          <p className="muted">{facts.submission_count ? `本会话已匹配 ${facts.submission_count} 份归档文件。` : '本会话未匹配归档提交物。'}</p>
        </details>
        </>}
      </header>

      <section className="panel stack study-dialogue study-section" id="studychat-dialogue">
        <div className="row"><h2>完整人机交互过程</h2><span className="muted">{facts.turn_count} 轮 · {facts.message_count} 条消息</span></div>
        {pages}
        <ErrorNotice text={conversation.error || error} />
        {attachment && <article className="notice" id="studychat-other-evidence"><b>关联事件 #{attachment.sequence_no} · {eventNames[attachment.type] || attachment.type}</b></article>}
        {conversation.loading && <Busy />}
        {conversation.data?.items.length === 0 && <Empty>这条会话没有对话记录。</Empty>}
        {!excluded && quality.transcripts.some(chat => !chat.in_scope) && <p className="notice">
          完整展示归档对话。缺轮或题目关联待确认的原始分组会标明，现场评价仅用通过检查的分组。</p>}
        {[...rounds].map(([turn, items]) => {
          const notes = items.flatMap(item => (annotations.get(item.id) ?? []).map(note => ({ item, note })))
          return <div className="study-round" data-round={turn} key={turn}>
            <div className="study-round-messages">
              {items.map(item => {
                const labels = report?.ai_behaviors?.filter(note => note.event_ids[0] === item.id) ?? []
                return <article id={'turn-' + item.id} data-event-id={item.id} data-turn={item.turn} tabIndex={-1}
                  className={`study-turn study-${item.role}${item.id === target ? ' study-target' : ''}`} key={item.id}>
                  <div className="study-turn-head"><strong>{item.role === 'student' ? '学生' : 'AI 回复'}</strong><span>第 {item.turn} 轮</span>
                    <span className="study-event-code">{anchors.get(item.id)}</span>
                    {item.conversation != null && <small>对话 {item.conversation} · 原始轮次 {item.source_turn}</small>}
                  </div>
                  {report?.out_of_task_event_ids?.includes(item.id) && <Badge tone="warn">与该作业题目不匹配 · 不参与评分</Badge>}
                  {!excluded && quality.excluded_event_ids.includes(item.id) && <Badge tone="warn">
                    {quality.transcripts.find(chat => chat.conversation === item.conversation)?.incomplete ? '原始会话缺轮' : '题目关联待确认'}
                  </Badge>}
                  {item.text.length > 950 ? <details className="study-long"><summary><strong className="study-msg-open">展开完整消息</strong>
                    <strong className="study-msg-close">收起完整消息</strong><span>{item.text.replace(/\s+/g, ' ').slice(0, 125)} …</span></summary>
                    <div className="study-text" data-full-message>{item.text}</div></details>
                    : <div className="study-text" data-full-message>{item.text || '（空消息）'}</div>}
                  {!!labels.length && <ul className="study-behaviors" aria-label="AI 协同行为">{labels.map((note, index) =>
                    <li key={index}><Badge>{behaviorNames[note.behavior]}</Badge><span>{note.text}</span></li>)}</ul>}
                </article>
              })}
            </div>
            <aside className={'study-round-notes' + (notes.length ? '' : ' is-empty')} aria-label={`第 ${turn} 轮的对话批注`}>
              {notes.map(({ item, note }) => <div key={note.id} data-anchor-event={item.id} className={`study-note dim-${note.dimension} note-${note.type}`}>
                <div className="study-note-caption">{annotationNames[note.type] ?? note.type} · {dimensionNames[note.dimension]}</div>
                <p>{note.text}</p><EvidenceLinks ids={note.evidence_refs.map(ref => ref.event_id)} locate={locate} />
              </div>)}
            </aside>
          </div>
        })}
        {pages}
      </section>

      {!excluded && report && <>
      <section className="panel study-section" id="study-evaluation">
        <h2>{report?.legacy_rubric ? '历史四维报告' : '五维过程评价'}</h2>
        <ErrorNotice text={reportData.error} />{reportData.loading && <Busy />}
        {!chosen && <Empty>此会话尚无五维报告。可从数据集评价开始分析。</Empty>}
        {report?.provider === 'rules' && !report.legacy_rubric && <p className="notice">
          {report.method === 'rules' ? '本次由本地规则评价（模型调用 0 次）。' : '本版采用本地规则结果。'}现有记录为 {facts.turn_count} 轮对话。
          {coverage && coverage.scored_count < coverage.minimum && `目前能从规则证据评定 ${coverage.scored_count} 项，至少评定 ${coverage.minimum} 项才会显示过程总分。`}
          {report.can_review && <> <a href="#teacher-review" onClick={event => { event.preventDefault(); jump('teacher-review') }}>查看教师复核</a></>}
        </p>}
        <div className="study-dimension-list">{report?.dimensions.map(item => <article className={`study-dimension dim-${item.dimension}`} key={item.dimension}>
          <div className="row"><h3>{dimensionNames[item.dimension]}</h3><Badge tone={item.state === 'insufficient_evidence' ? 'warn' : 'neutral'}>
            {item.state === 'scored' ? `${item.level} / ${nativeFive ? 5 : 3} · 已评分` : statusNames[item.state]}</Badge></div>
          <p>{item.rationale}</p><small>{new Set(item.event_ids).size} 条证据</small><EvidenceLinks ids={item.event_ids} locate={locate} />
        </article>)}</div>
      </section>

      <section className="panel study-section" id="study-performance">
        <h2>综合表现</h2>
        <div className="study-performance-metrics">
          <article><span>过程总分</span><strong>{processDisplay(report.process_score, report.process_score_five, '过程证据不足')}</strong>
            {coverage && <><div>{coverage.scored_count} / {coverage.total} 维可评分</div><progress max={coverage.total} value={coverage.scored_count} aria-label="五维覆盖率" /></>}
          </article>
          {sourceScore != null && <article><span>来源成果分</span><strong>{sourceScore}</strong><small>StudyChat 原始作业成绩 · 百分制</small></article>}
          {systemScore != null && <article><span>{localOutcome ? '本地成果分' : '系统成果分'}</span><strong>{systemScore}</strong>
            <small>{localOutcome ? '教师按提交材料复核' : '系统成果量规'} · 百分制</small></article>}
        </div>
        {sourceScore == null && systemScore == null && <p className="muted study-missing-score">该记录暂无可用成果成绩。</p>}
        {coverage && <p className="muted study-formula">{nativeFive ? '取可评分维度的平均分' : '历史等级 0–3，过程总分由旧百分制折算为五分制'}，至少 {coverage.minimum} 维可评分。</p>}
        {localDetails && <details><summary>本地成果评分明细</summary><ul>{localDetails.items.map(item =>
          <li key={item.item_id}><b>{item.title}</b> · {item.score == null ? '证据不足' : `${item.score} / ${item.max_score}`}
            {' · '}权重 {item.weight}%<p>{item.reason}</p></li>)}</ul></details>}
        <div className="study-integrated-diagnosis"><h3>二维诊断</h3>
          <p>{report?.diagnosis.label ?? '当前可用分数不足以形成二维诊断。'}</p>
          {report?.diagnosis.label && <small className="muted">成果依据 · {outcomeName(report.outcome_basis)}</small>}
        </div>
        {report?.teaching_diagnosis && <div className="study-integrated-diagnosis"><h3>教学诊断</h3><p>{report.teaching_diagnosis.text}</p>
          <EvidenceLinks ids={report.teaching_diagnosis.event_ids} locate={locate} /></div>}
      </section>

      <section className="panel study-section" id="study-guidance">
        <h2>教学建议</h2>
        {report?.provider === 'rules' && !report.teacher_recommendations?.length && !report.student_recommendations?.length &&
          <p className="muted">本地规则尚未找到可用于个别建议的行为。教师可以请学生补充一条自己做过的对照或修改，记录做法、理由和结果。</p>}
        <div className="study-guidance-grid">
          <div><h3>给教师的教学建议</h3>
            {report?.teacher_recommendations?.length ? <ol className="study-guidance-list">{report.teacher_recommendations.map((item, i) =>
              <li key={i} className={`dim-${item.dimension}`}><strong>{dimensionNames[item.dimension]}</strong>
                <dl><dt>观察</dt><dd>{item.observation}</dd><dt>教学判断</dt><dd>{item.judgment}</dd>
                  <dt>指导动作</dt><dd>{item.action}</dd><dt>后续检查</dt><dd>{item.follow_up}</dd></dl>
                <EvidenceLinks ids={item.event_ids} locate={locate} turns={report.evidence_turns} />
              </li>)}</ol> : report?.guidance_basis === 'teacher_edit' && report.teaching_suggestions.length ?
                <ol>{report.teaching_suggestions.map((text, i) => <li key={i}>{text}</li>)}</ol> :
                <p className="muted">{report?.provider === 'rules' ? '可从教师复核添加基于具体轮次的观察和建议。' : '当前版本暂无结构化教师建议。'}</p>}
          </div>
          <div><h3>给学生的改进建议</h3>
            {report?.student_recommendations?.length ? <ol className="study-guidance-list">{report.student_recommendations.map((item, i) =>
              <li key={i} className={`dim-${item.dimension}`}><strong>{dimensionNames[item.dimension]}</strong>
                <p>{item.action}</p>{item.example && <small>{item.example}</small>}<EvidenceLinks ids={item.event_ids} locate={locate} turns={report.evidence_turns} /></li>)}</ol>
              : <p className="muted">{report?.provider === 'rules' ? '任选一个采用或修改过的建议，写一句话说明你为什么这么做。' : '当前版本暂无结构化学生建议。'}</p>}
          </div>
        </div>
        {!!report?.transfer_tasks?.length && <div className="study-transfer"><h3>迁移练习</h3>
          <p className="muted">先完成上面的动作建议，再用新情境检查方法能否迁移。</p>
          <ol className="study-guidance-list">{report.transfer_tasks.map((item, i) => <li key={i} className={`dim-${item.dimension}`}>
            <strong>{item.title}</strong><p>{item.prompt}</p><small>完成标准：{item.success_criteria}</small>
            <EvidenceLinks ids={item.event_ids} locate={locate} turns={report.evidence_turns} />
          </li>)}</ol></div>}
        {report?.guidance_basis === 'archived_report_editorial' && <small className="muted">依据历史报告和原始轮次整理的课堂建议，可在报告版本中查阅模型原建议。</small>}
        {report?.guidance_basis === 'teacher_edit' && <p className="muted">本版建议由教师复核编辑。</p>}
        {report?.guidance_basis === 'local_rule_evidence' && <p className="muted">本地规则依据所引轮次整理。教师可在复核时补充判断。</p>}
      </section>

      <section className="panel stack study-section" id="study-data">
        <h2>数据说明和教师复核</h2>
        {systemScore == null && !localDetails && <p>本次未对归档提交物重新执行成果量规。</p>}
        {report && <div className="chips"><Badge>{modelNames[report.model] ?? report.model}</Badge><Badge>{report.rubric_version}</Badge>
          <Badge>{report.kind === 'reviewed' ? '已复核' : '待教师复核'}</Badge>{report.degraded && <Badge tone="warn">规则降级</Badge>}</div>}
        {report.provider === 'deepseek' && report.provider_call && <p>
          deepseek / {report.model} · 调用 {report.provider_call.actual_model_calls ?? report.provider_call.stages?.length ?? '未知'} 次
          {' · '}{report.provider_call.segments ?? '未知'} 个分片 · 真实返回已校验
        </p>}
        {session.reports.length > 0 && <label>报告版本<select aria-label="报告版本" value={chosen?.reportId ?? ''} onChange={e => {
          const entry = session.reports.find(item => item.id === e.target.value)
          setSelection(entry ? { runId: entry.run_id, reportId: entry.id } : undefined)
        }}>
          {!chosen && <option value="">选择报告</option>}
          {chosen && !session.reports.some(item => item.id === chosen.reportId) && <option value={chosen.reportId}>本次分析指定版本</option>}
          {session.reports.map(item => <option value={item.id} key={item.id}>{item.legacy_rubric ? '历史四维报告' : '五维量规'} · {methodNames[item.method]} · v{item.version}</option>)}
        </select></label>}
        {report && <details><summary>证据覆盖、来源和原始建议</summary>
          <ul>{[...report.warnings, ...(report.limitations ?? [])].map((item, i) => <li key={i}>{item}</li>)}</ul>
          {report.outcome_display_projection && <p className="muted">源成绩诊断按旧报告冻结阈值补充展示，存档报告正文保持原样。</p>}
          {report.teaching_suggestions.length > 0 && <ol>{report.teaching_suggestions.map((item, i) => <li key={i}>{item}</li>)}</ol>}
          {report.parent_run_id && <a href={'#/report/' + report.parent_run_id}>查看重新分析前的报告</a>}
        </details>}
        {report?.system_quality_review && <details><summary>独立证据质检记录</summary><p>{report.system_quality_review.reason}</p>
          <ul>{report.system_quality_review.changes.map((item, i) => <li key={i}>{item.reason}<EvidenceLinks ids={item.event_ids} locate={locate} /></li>)}</ul>
          <a href={'#/report/' + report.run_id + '/' + report.system_quality_review.system_parent_id}>查看该次 DeepSeek 原始报告</a>
        </details>}
        {report?.can_review && review && <div id="teacher-review">{review(report, locate, () => { setSelection(undefined); refresh() })}</div>}
      </section>
      </>}
      {!excluded && !report && <section className="panel stack"><ErrorNotice text={reportData.error} />
        {reportData.loading ? <Busy /> : <Empty>该会话已通过本地资格检查。可从数据集评价使用已授权的 DeepSeek 生成正式报告。</Empty>}
      </section>}
    </div>
    <aside className="study-page-outline" aria-label="会话页面导航">
      {sections.filter(([id]) => report || id === 'study-question' || id === 'studychat-dialogue').map(([id, label]) =>
        <button type="button" className="link-button" key={id} onClick={() => jump(id)}>
          {excluded && id === 'study-question' ? '数据质量结论' : label}</button>)}
      {report?.process_score != null && <div><span>过程分</span><strong>{processDisplay(report.process_score, report.process_score_five)}</strong></div>}
      {report?.display_outcome_score != null && <div><span>{outcomeName(report.outcome_basis)}</span><strong>{report.display_outcome_score}</strong></div>}
    </aside>
  </div>
}
