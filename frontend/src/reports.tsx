import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { api, message, terminal } from './api'
import type { EventDetail, EventSummary, Job, Page, Report, SessionDetail } from './api'
import { StudyChatView } from './studychat'
import type { ReportReview, ReportSelection } from './studychat'
import { Badge, Busy, Empty, ErrorNotice, Heading, JobCard, Pager, dimensionNames, displayScore, eventNames, fivePointRubric, outcomeName, processDisplay,
  methodNames, statusNames, time, useData } from './ui'

type Artifact = { id: string, kind: string, version: number, mime_type: string, content: string, preview_truncated: boolean }

export function Timeline({ id, selected, onSelect }: { id: string, selected: string, onSelect: (id: string) => void }) {
  const [page, setPage] = useState(1)
  const records = useData<Page<EventSummary>>('/sessions/' + id + '/timeline?page=' + page)
  const event = useData<EventDetail>(selected ? '/sessions/' + id + '/events/' + selected : null)
  const [artifactId, setArtifactId] = useState('')
  const artifact = useData<Artifact>(artifactId ? '/sessions/' + id + '/artifacts/' + artifactId : null)
  function pick(eventId: string) { setArtifactId(''); onSelect(eventId) }
  useEffect(() => setArtifactId(''), [selected])
  return <section className="panel stack">
    <h2>事件时间线</h2>
    <ErrorNotice text={records.error || event.error || artifact.error} />
    {event.data && <article className="evidence-focus" tabIndex={-1} ref={element => {
      if (element && document.activeElement !== element) element.focus({ preventScroll: true })
    }}>
      <strong>证据 #{event.data.sequence_no} · {eventNames[event.data.type] || event.data.type}</strong>
      <p className="muted">{time(event.data.occurred_at)}</p>
      <pre>{JSON.stringify(event.data.payload, null, 2)}</pre>
      {Array.isArray(event.data.payload.artifact_refs) && <div className="chips">{
        event.data.payload.artifact_refs.map((ref: string, index: number) => <button key={ref} className="secondary"
          onClick={() => setArtifactId(ref)}>查看交付物 {index + 1}</button>)}
      </div>}
      {artifact.loading && <Busy />}
      {artifact.data && <div className="divider"><b>交付物 · {artifact.data.mime_type}</b>
        <pre>{artifact.data.content}</pre>
        {artifact.data.preview_truncated && <span className="muted">预览显示前 50,000 个字符。</span>}</div>}
      <button className="secondary" onClick={() => pick('')}>关闭事件详情</button>
    </article>}
    {event.loading && selected && <Busy />}
    {records.loading && <Busy />}
    {records.data?.items.length === 0 && <Empty>当前会话没有事件。</Empty>}
    <ol className="timeline">{records.data?.items.map(item => <li key={item.id}>
      <button className={'event-button ' + (item.id === selected ? 'event-active' : '')} aria-pressed={item.id === selected}
        onClick={() => pick(item.id)}>
        <b>#{item.sequence_no} · {eventNames[item.type] || item.type}</b>
        <span>{item.excerpt}</span><small>{time(item.occurred_at)}</small>
      </button>
    </li>)}</ol>
    {records.data && <Pager page={page} total={records.data.total} size={20} set={setPage} />}
  </section>
}

export function SessionView({ id, review, selectedReport }: { id: string, review?: ReportReview, selectedReport?: ReportSelection }) {
  const [refresh, setRefresh] = useState(0)
  const result = useData<SessionDetail>('/sessions/' + id, refresh)
  const [selected, setSelected] = useState('')
  const [error, setError] = useState('')
  useEffect(() => {
    const data = result.data
    if (data?.source_type === 'live' && data.status === 'submitted' &&
        !data.reports.length && (!data.jobs.length || data.jobs.some(job => !terminal(job.status)))) {
      const timer = setTimeout(() => setRefresh(old => old + 1), 1100)
      return () => clearTimeout(timer)
    }
  }, [result.data])
  async function retry(job: Job) {
    setError('')
    try { await api('/jobs/' + job.id + '/retry-failed', 'POST'); setRefresh(old => old + 1) }
    catch (e) { setError(message(e)) }
  }
  if (!result.data) return <><ErrorNotice text={result.error} />{result.loading && <Busy />}</>
  const session = result.data
  if (session.studychat) return <StudyChatView session={session} review={review} initial={selectedReport} refresh={() => setRefresh(old => old + 1)} />
  return <>
    <Heading title={session.task_title}>{session.source_type === 'live' ? '真人任务' : '数据集记录'} · {statusNames[session.status] || session.status}</Heading>
    {session.task_instructions && <details className="panel session-task"><summary>任务说明</summary><pre>{session.task_instructions}</pre></details>}
    <div className="grid two align-start">
      <Timeline id={id} selected={selected} onSelect={setSelected} />
      <section className="panel stack"><h2>评价报告</h2>
        <ErrorNotice text={error} />
        {!session.reports.length && <Empty>{session.source_type === 'live' && session.status === 'submitted'
          ? (session.jobs.length ? '报告完成且发布后，会在这里显示。' : '提交已冻结，等待后台领取评价。') : '尚无可查看的报告。'}</Empty>}
        {session.reports.map(report => <a className="list-card" href={'#/report/' + report.run_id} key={report.id}>
          <strong>{methodNames[report.method]} · v{report.version}{report.legacy_rubric && ' · 旧量规'}</strong>
          <span>{report.diagnosis.label || '暂不形成二维诊断'}</span>
          <span className="muted">过程 {processDisplay(report.process_score, report.process_score_five)}{report.outcome_score != null &&
            ` · ${outcomeName(report.outcome_basis)} ${report.outcome_score}`}</span>
        </a>)}
        {session.jobs.map(job => <div key={job.id}><JobCard job={job} />
          {job.failed_count > 0 && <button className="secondary" onClick={() => void retry(job)}>重试失败记录</button>}</div>)}
        {!!Object.keys(session.external_scores).length && <details><summary>来源成绩（对照字段）</summary>
          <ul className="plain">{Object.entries(session.external_scores).map(([field, value]) =>
            <li key={field}>{field} · {value}</li>)}</ul></details>}
      </section>
    </div>
  </>
}

export function ReportView({ runId, versionId, review }: {
  runId: string, versionId?: string,
  review?: (report: Report, onEvidence: (id: string) => void, refresh: () => void) => ReactNode,
}) {
  const [refresh, setRefresh] = useState(0)
  const [selected, select] = useState('')
  const result = useData<Report & { error?: { message: string } }>('/evaluation-runs/' + runId
    + (versionId ? '?report_id=' + encodeURIComponent(versionId) : ''), refresh)
  const report = result.data
  useEffect(() => {
    if (report && !report.id && ['queued', 'running'].includes(report.status)) {
      const timer = setTimeout(() => setRefresh(old => old + 1), 900)
      return () => clearTimeout(timer)
    }
  }, [report])
  if (!report?.id) return <>
    <Heading title="评价报告" />
    <ErrorNotice text={result.error || report?.error?.message} />
    {result.loading && <Busy />}
    {report && <Empty>{statusNames[report.status] || report.status}。报告发布后会在这里显示。</Empty>}
  </>
  const locate = (id: string) => {
    select(id)
    document.getElementById('report-evidence')?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }
  return <>
    <Heading title="评价报告" actions={<div className="toolbar">
      {review && report.can_review && <button className="secondary" onClick={() => document.getElementById('teacher-review')?.scrollIntoView({ behavior: 'smooth' })}>前往教师复核</button>}
      <a className="button secondary" href={'#/session/' + report.session_id}>查看会话</a></div>}>
      {report.source_type === 'dataset' ? '数据集记录' : '真人任务'} · {methodNames[report.method]} · {statusNames[report.kind]} v{report.version}
      {report.legacy_rubric && ' · 历史四维报告'}
    </Heading>
    <div className="stack">
      {!!report.warnings.length && <div className="notice warn">{report.warnings.map(warning => <div key={warning}>{warning}</div>)}</div>}
      <section className="panel report-summary">
        <div><span>过程分</span><strong>{processDisplay(report.process_score, report.process_score_five, '过程证据不足')}</strong>
          {!fivePointRubric(report.rubric_version) && <small>按历史百分制折算</small>}</div>
        {(report.display_outcome_score != null || report.source_type === 'live') && <div>
          <span>{outcomeName(report.outcome_basis)}</span>
          <strong>{displayScore(report.display_outcome_score, '成果证据不足')}</strong></div>}
        {report.outcome_basis === 'local_review' && report.source_outcome_percent != null &&
          <div><span>来源成果分</span><strong>{report.source_outcome_percent}</strong></div>}
        <div><span>二维诊断</span><strong>{report.diagnosis.label ?? '暂不形成二维诊断'}</strong></div>
      </section>
      <div className="report-grid align-start">
        <div id="report-evidence"><Timeline id={report.session_id} selected={selected} onSelect={select} /></div>
        <div className="stack">
          <section className="panel"><h2>{report.legacy_rubric ? '历史四维报告' : '五维过程评价'}</h2>
            <div className="grid two">{report.dimensions.map(item => <article className="metric" key={item.dimension}>
              <h3>{dimensionNames[item.dimension]}</h3>
              <Badge tone={item.state === 'insufficient_evidence' ? 'warn' : 'neutral'}>
                {item.state === 'scored' ? item.level + ' / ' + (fivePointRubric(report.rubric_version) ? 5 : 3) : statusNames[item.state]}
              </Badge>
              <p>{item.rationale}</p>
              {item.event_ids.map((event, i) => <button className="link-button" key={event} onClick={() => locate(event)}>定位证据 {i + 1}</button>)}
            </article>)}</div>
          </section>
          <section className="panel"><h2>证据批注</h2>
            {!report.annotations.length && <Empty>可用证据不足，当前没有批注。</Empty>}
            {report.annotations.map(item => <article key={item.id} className="annotation">
              <Badge tone={item.type === 'rule_llm_conflict' ? 'warn' : 'neutral'}>{item.type === 'rule_llm_conflict' ? '规则与模型冲突' : dimensionNames[item.dimension]}</Badge>
              <p>{item.text}</p>
              <ul className="plain">{item.evidence_refs.map(ref => <li key={ref.event_id}>
                <button className="link-button" onClick={() => locate(ref.event_id)}>定位事件</button> <span className="muted">{ref.excerpt}</span>
              </li>)}</ul>
            </article>)}
          </section>
          {(report.outcome.score != null || report.source_type === 'live' || report.outcome.basis === 'local_review') && <section className="panel">
            <h2>{report.outcome.basis === 'local_review' ? '本地成果分项' : '系统成果分项'}</h2>
            <ul className="plain">{report.outcome.items.map(item => <li key={item.item_id}>
              <b>{item.title}</b> · {item.score == null ? '缺少成果证据' : `${item.score} / ${item.max_score}`}
              <div className="muted">{item.reason}</div>
            </li>)}</ul>
          </section>}
          {!!report.teaching_suggestions.length && <section className="panel"><h2>教学建议</h2>
            <ul>{report.teaching_suggestions.map(item => <li key={item}>{item}</li>)}</ul>
          </section>}
          {review && report.can_review && <div id="teacher-review">{review(report, locate, () => setRefresh(old => old + 1))}</div>}
          <details className="panel"><summary>版本与提供器</summary>
            <p>{report.provider} · {report.model}</p>
            <p>任务 {report.task_version}</p>
            <p>量规 {report.rubric_version} · 阈值 {report.threshold_version} · 提示词 {report.prompt_version} · 规则 {report.ruleset_version}</p>
            {report.parent_id && <a href={'#/report/' + runId + '/' + report.parent_id}>查看上一版本</a>}
          </details>
        </div>
      </div>
    </div>
  </>
}
