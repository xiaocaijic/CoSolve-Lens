import { useState } from 'react'
import type { FormEvent } from 'react'
import { api, message } from './api'
import type { Dimension, EventSummary, Page, Report } from './api'
import { Badge, Busy, Empty, ErrorNotice, Heading, Pager, dimensionNames, eventNames, fiveDimensionNames, fivePointRubric, methodNames, time, useData } from './ui'

type QueueItem = {
  id: string, run_id: string, task: string, method: string, source_type: string, reviewed: boolean,
  conflict: boolean, insufficient: boolean, demo_only: boolean,
}
type History = { id: string, version: number, decision: string | null, reason: string | null, created_at: string,
  changes: { target: string, before: unknown, after: unknown }[] }

export function ReviewQueue() {
  const [state, setState] = useState('unreviewed')
  const [page, setPage] = useState(1)
  const queue = useData<Page<QueueItem>>('/reviews/queue?state=' + state + '&page=' + page)
  const filters = { unreviewed: '未复核', conflict: '规则冲突', low_confidence: '低置信度', insufficient: '证据不足',
    reviewed: '已复核', all: '全部' }
  return <>
    <Heading title="教师复核">核对原始记录，确认或修改系统评价。修改会生成独立版本。</Heading>
    <section className="panel stack"><div className="toolbar">
      <label>复核状态<select value={state} onChange={e => { setState(e.target.value); setPage(1) }}>
        {Object.entries(filters).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
      </select></label></div>
      <ErrorNotice text={queue.error} />{queue.loading && <Busy />}
      {queue.data?.items.length === 0 && <Empty>没有符合当前筛选条件的报告。</Empty>}
      {queue.data?.items.map(item => <a className="list-card" key={item.id} href={'#/report/' + item.run_id + '/' + item.id}>
        <div className="row"><strong>{item.task}</strong><Badge tone={item.conflict ? 'warn' : 'neutral'}>{
          item.conflict ? '待裁决冲突' : item.reviewed ? '已复核' : '待复核'}</Badge></div>
        <span className="muted">{item.source_type === 'dataset' ? '数据集' : '真人任务'} · {methodNames[item.method]}
          {item.insufficient && ' · 证据不足'}{item.demo_only && ' · 工程演示'}</span>
      </a>)}
      {queue.data && <Pager page={page} size={20} total={queue.data.total} set={setPage} />}
    </section>
  </>
}

const reasons: Record<string, string> = {
  NO_ORGANIZATION_OPPORTUNITY: '任务没有组织解题步骤的机会', NO_VERIFIABLE_ARTIFACT: '任务没有可验证交付物',
  NO_DIAGNOSTIC_EVENT: '未观察到诊断机会', NO_AI_USE: '未观察到解题过程中的 AI 使用',
  RULES_CANNOT_JUDGE_SEMANTICS: '规则无法判断语义', MISSING_TASK: '缺少任务说明',
  MISSING_RUN_LOG: '缺少运行记录', SYNTHETIC_EXECUTION: '仅有模拟运行',
  UNVERIFIED_EXECUTION: '执行未经核验', UNALIGNED_ARTIFACT_VERSION: '执行与提交版本未对齐',
  CONFLICTING_RECORDS: '记录冲突', MISSING_AI_LOG: 'AI 记录缺失', MISSING_DIAGNOSTIC_LOG: '诊断记录缺失',
  MISSING_PROBLEM_EVIDENCE: '缺少问题理解证据', MISSING_DISCERNMENT_EVIDENCE: '缺少信息甄别证据',
  MISSING_REASONING_EVIDENCE: '缺少分析推理证据', MISSING_RESULT_EVIDENCE: '缺少结果评价证据',
  MISSING_STRATEGY_EVIDENCE: '缺少策略调整证据',
}
const naReason: Record<string, string> = {
  organization: 'NO_ORGANIZATION_OPPORTUNITY', verification: 'NO_VERIFIABLE_ARTIFACT',
  diagnosis: 'NO_DIAGNOSTIC_EVENT', ai_integration: 'NO_AI_USE',
}
for (const [name, label] of Object.entries(fiveDimensionNames)) {
  const code = `NO_${name.toUpperCase()}_OPPORTUNITY`
  naReason[name] = code
  reasons[code] = `没有${label}机会`
}

export function ReviewForm({ report, locate, refresh, stayOnSession = false }: {
  report: Report, locate: (id: string) => void, refresh: () => void, stayOnSession?: boolean,
}) {
  const conflicts = report.conflicts.filter(conflict => conflict.state === 'pending_review')
  const levels = fivePointRubric(report.rubric_version) ? [1, 2, 3, 4, 5] : [0, 1, 2, 3]
  const [decision, setDecision] = useState(conflicts.length ? 'amend' : 'confirm')
  const [resolution, setResolution] = useState('accept_rule')
  const [reason, setReason] = useState('')
  const [dimension, setDimension] = useState('')
  const [state, setState] = useState<Dimension['state']>('scored')
  const [level, setLevel] = useState(2)
  const [special, setSpecial] = useState('MISSING_RUN_LOG')
  const [rationale, setRationale] = useState('')
  const [annotation, setAnnotation] = useState('')
  const [recommendations, setRecommendations] = useState(report.teaching_suggestions.join('\n'))
  const [changeSuggestions, setChangeSuggestions] = useState(false)
  const [selected, setSelected] = useState<string[]>([])
  const [evidencePage, setEvidencePage] = useState(1)
  const [release, setRelease] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const history = useData<{ items: History[], parent_run_id: string | null }>('/evaluation-runs/' + report.run_id + '/versions')
  const events = useData<Page<EventSummary>>(decision === 'amend' ? '/sessions/' + report.session_id + '/timeline?page_size=25&page=' + evidencePage : null)
  const requiresEdit = resolution === 'adjust_judgment' && !!conflicts.length
  const hasConflict = !!conflicts.length
  const editDimension = requiresEdit ? conflicts[0].dimension : dimension

  function chooseDimension(value: string) {
    setDimension(value)
    const current = report.dimensions.find(item => item.dimension === value)
    if (!current) return
    setState(current.state); setLevel(current.level ?? 2); setSpecial(current.reason ?? 'MISSING_RUN_LOG')
    setRationale(current.rationale); setSelected(current.event_ids)
  }

  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(''); setNotice('')
    const amend = decision === 'amend'
    try {
      const adjudication = hasConflict && resolution === 'accept_rule' ? [...new Set(conflicts.flatMap(item => item.rule_event_ids))] : selected
      const dimensions = amend && editDimension ? [{
        dimension: editDimension, state, level: state === 'scored' ? level : null,
        reason: state === 'scored' ? null : special, rationale, event_ids: selected,
      }] : []
      const edited = await api<Report & { rerun_job_id?: string, review?: { draft?: boolean } }>('/evaluation-runs/' + report.run_id + '/reviews', 'POST', {
        expected_report_version: report.id, decision, reason, dimensions, release,
        ...(amend && hasConflict ? { conflict_resolution: resolution, adjudication_event_ids: adjudication } : {}),
        ...(amend && annotation.trim() && editDimension ? { annotations: [{
          dimension: editDimension, type: 'turning_point', text: annotation, event_ids: selected,
        }] } : {}),
        ...(amend && changeSuggestions ? { teaching_suggestions: recommendations.split('\n').map(line => line.trim()).filter(Boolean) } : {}),
      })
      if (edited.rerun_job_id) {
        setNotice('已提交重评。可前往队列查看新报告。')
      } else if (edited.review?.draft) {
        setNotice('极端等级缺少足够证据，已保存为未发布草稿。')
      }
      if (!stayOnSession || edited.review?.draft || edited.rerun_job_id) location.hash = '#/report/' + report.run_id + '/' + edited.id
      refresh()
    } catch (e) { setError(message(e)) } finally { setBusy(false) }
  }
  function toggle(id: string) {
    setSelected(old => old.includes(id) ? old.filter(item => item !== id) : [...old, id])
  }
  return <section className="panel stack"><h2>教师复核</h2>
    {report.review && <div className="notice"><strong>{report.review.reviewer} · {report.review.decision === 'rerun' ? '已退回重评' : '已记录复核'}</strong>
      <p>{report.review.reason}</p></div>}
    <ErrorNotice text={error || events.error || history.error} />
    {notice && <div className="notice">{notice}</div>}
    {!report.effective && <Empty>该版本可用于查阅，当前有效版本可从复核队列打开。</Empty>}
    {report.effective && <form className="stack" onSubmit={submit}>
      <label>复核结论<select value={decision} onChange={e => setDecision(e.target.value)}>
        <option value="confirm" disabled={hasConflict}>确认</option><option value="amend">修改后确认</option><option value="rerun">退回重评</option>
      </select></label>
      {hasConflict && decision === 'amend' && <div className="notice warn stack">
        {conflicts.map(item => <div key={item.dimension}>
          <b>{dimensionNames[item.dimension]} · 待复核</b><p>{item.candidate.text}</p>
          {item.candidate.event_ids.map(id => <button type="button" className="link-button" key={id} onClick={() => locate(id)}>候选证据</button>)}
          {item.rule_event_ids.map((id, i) => <button type="button" className="link-button" key={id} onClick={() => locate(id)}>规则证据 {i + 1}</button>)}
        </div>)}
        <label>冲突裁决<select value={resolution} onChange={e => {
          setResolution(e.target.value)
          chooseDimension(e.target.value === 'adjust_judgment' ? conflicts[0].dimension : '')
        }}><option value="accept_rule">规则事实有效</option><option value="adjust_judgment">根据证据调整判断</option></select></label>
      </div>}
      {decision === 'amend' && <>
        {!requiresEdit && <label>修改维度<select value={dimension} onChange={e => chooseDimension(e.target.value)}>
          <option value="">无需额外修改</option>{report.dimensions.map(({ dimension: value }) =>
            <option key={value} value={value} disabled={hasConflict && resolution === 'accept_rule' && conflicts.some(item => item.dimension === value)}>{dimensionNames[value]}</option>)}
        </select></label>}
        {editDimension && <>
          <label>维度状态<select value={state} onChange={e => {
            const next = e.target.value as Dimension['state']
            setState(next)
            setSpecial(next === 'not_applicable' ? naReason[editDimension] : 'RULES_CANNOT_JUDGE_SEMANTICS')
          }}>
            <option value="scored">已评分</option><option value="not_applicable">不适用</option><option value="insufficient_evidence">证据不足</option>
          </select></label>
          {state === 'scored' ? <label>等级<select aria-label="等级" value={level} onChange={e => setLevel(Number(e.target.value))}>
            {levels.map(item => <option value={item} key={item}>{item}</option>)}
          </select></label> : <label>原因码<select value={special} onChange={e => setSpecial(e.target.value)}>
            {Object.entries(reasons).filter(([value]) => state === 'not_applicable' ? value === naReason[editDimension] :
              !Object.values(naReason).includes(value)).map(([value, label]) => <option value={value} key={value}>{label}</option>)}
          </select></label>}
          <label>维度判断依据<textarea required value={rationale} onChange={e => setRationale(e.target.value)} maxLength={2000} /></label>
          <details><summary>引用证据（已选 {selected.length}）</summary>
            <div className="evidence-options">{events.data?.items.map(item => <div className="toolbar" key={item.id}>
              <label className="check"><input type="checkbox" checked={selected.includes(item.id)} onChange={() => toggle(item.id)} />
                #{item.sequence_no} {eventNames[item.type] || item.type}</label>
              <button type="button" className="link-button" onClick={() => locate(item.id)}>查看</button>
            </div>)}</div>
            {events.data && <Pager page={evidencePage} total={events.data.total} size={25} set={setEvidencePage} />}
          </details>
          <label>追加教师批注<textarea value={annotation} onChange={e => setAnnotation(e.target.value)} maxLength={2000} /></label>
        </>}
        <label className="check"><input type="checkbox" checked={changeSuggestions} onChange={e => setChangeSuggestions(e.target.checked)} />修改教学建议</label>
        {changeSuggestions && <label>每行一条教学建议<textarea value={recommendations} onChange={e => setRecommendations(e.target.value)} /></label>}
      </>}
      <label>复核理由<textarea required value={reason} onChange={e => setReason(e.target.value)} maxLength={3000} /></label>
      {report.source_type === 'live' && decision !== 'rerun' && <label className="check"><input type="checkbox"
        checked={release} onChange={e => setRelease(e.target.checked)} />向学生发布此版本</label>}
      <button disabled={busy || !reason.trim()}>保存复核</button>
    </form>}
    {report.effective && !report.demo_only && (hasConflict ?
      <p className="muted">裁决过程评价冲突后可以填写本地成果评分。</p> :
      <LocalOutcomeForm report={report} refresh={refresh} stayOnSession={stayOnSession} />)}
    <details><summary>版本审计</summary><ul className="plain">{history.data?.items.map(item => <li key={item.id}>
      <a href={'#/report/' + report.run_id + '/' + item.id}>v{item.version}</a> · {time(item.created_at)}
      {item.reason && <p>{item.reason}</p>}
      {!!item.changes.length && <details><summary>查看前后值</summary>
        <pre>{JSON.stringify(item.changes, null, 2)}</pre></details>}
    </li>)}</ul>{history.data?.parent_run_id && <a href={'#/report/' + history.data.parent_run_id}>查看重评前的运行</a>}</details>
  </section>
}

type OutcomeInputs = {
  rubric_version: string,
  criteria: { item_id: string, title: string, weight: number, guide: string }[],
  artifacts: { id: string, kind: string, version: number, mime_type: string }[],
}
type OutcomeEntry = { score: string, reason: string, artifact_ids: string[] }

function LocalOutcomeForm({ report, refresh, stayOnSession }: { report: Report, refresh: () => void, stayOnSession: boolean }) {
  const [open, setOpen] = useState(false)
  const inputs = useData<OutcomeInputs>(open ? `/evaluation-runs/${report.run_id}/local-outcome-inputs?report_id=${report.id}` : null)
  const evaluated = report.local_outcome ?? (report.outcome.basis === 'local_review' ? report.outcome : undefined)
  const [entry, setEntry] = useState<Record<string, OutcomeEntry>>(() => Object.fromEntries(
    evaluated?.items.map(item => [item.item_id, {
      score: item.score == null ? '' : String(item.score), reason: item.reason, artifact_ids: item.artifact_ids ?? [],
    }]) ?? []))
  const [previewId, setPreviewId] = useState('')
  const preview = useData<{ id: string, content: string, preview_truncated: boolean }>(previewId ? `/sessions/${report.session_id}/artifacts/${previewId}` : null)
  const [release, setRelease] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const blank = { score: '', reason: '', artifact_ids: [] }
  const update = (key: string, values: Partial<OutcomeEntry>) =>
    setEntry(old => ({ ...old, [key]: { ...(old[key] ?? blank), ...values } }))

  async function save(event: FormEvent) {
    event.preventDefault()
    if (!inputs.data) return
    setBusy(true); setError('')
    try {
      const edited = await api<Report>(`/evaluation-runs/${report.run_id}/reviews`, 'POST', {
        expected_report_version: report.id, decision: 'amend', reason: '教师按本地成果量规核对提交材料。', release,
        local_outcome: inputs.data.criteria.map(({ item_id }) => {
          const item = entry[item_id] ?? blank
          return { ...item, item_id, score: item.score === '' ? null : Number(item.score) }
        }),
      })
      if (!stayOnSession) location.hash = `#/report/${report.run_id}/${edited.id}`
      refresh()
    } catch (e) { setError(message(e)) } finally { setBusy(false) }
  }

  return <details onToggle={event => setOpen(event.currentTarget.open)}><summary>本地成果评价</summary>
    <div className="stack divider">
      <ErrorNotice text={error || inputs.error || preview.error} />
      {inputs.loading && open && <Busy />}
      {inputs.data?.artifacts.length === 0 && <Empty>此报告没有可核对的正式提交材料，暂不能进行本地成果评价。</Empty>}
      {inputs.data && inputs.data.artifacts.length > 0 && <>
        <p className="muted">在本机核对提交材料并评分。每项满分 100，三项都有分数时按权重计算总分。无法核验的项目可留空并写明原因。</p>
        <label>查看提交材料<select value={previewId} onChange={e => setPreviewId(e.target.value)}>
          <option value="">请选择材料</option>
          {inputs.data.artifacts.map((item, i) => <option key={item.id} value={item.id}>材料 {i + 1} · {item.mime_type}</option>)}
        </select></label>
        {preview.loading && <Busy />}
        {previewId && preview.data?.id === previewId && <div>
          <pre className="local-outcome-preview" tabIndex={0}>{preview.data.content}</pre>
          {preview.data.preview_truncated && <p className="muted">此处显示前 50,000 个字符。请从归档核对其余部分。</p>}
        </div>}
        <form className="stack" onSubmit={save}>
          {inputs.data.criteria.map(item => {
            const value = entry[item.item_id] ?? blank
            return <fieldset className="stack local-outcome-criterion" key={item.item_id}>
              <legend>{item.title} · 权重 {item.weight}%</legend><small>{item.guide}</small>
              <label>{item.title}分数（0–100）<input type="number" min={0} max={100} step={1} value={value.score}
                onChange={e => update(item.item_id, { score: e.target.value })} /></label>
              <label>{item.title}依据<textarea required maxLength={2000} value={value.reason}
                onChange={e => update(item.item_id, { reason: e.target.value })} /></label>
              <div className="evidence-options" aria-label={item.title + '引用材料'}>
                {inputs.data?.artifacts.map((artifact, i) => <label key={artifact.id} className="check">
                  <input type="checkbox" checked={value.artifact_ids.includes(artifact.id)} onChange={e => update(item.item_id, {
                    artifact_ids: e.target.checked ? [...value.artifact_ids, artifact.id] : value.artifact_ids.filter(id => id !== artifact.id),
                  })} />材料 {i + 1}</label>)}
              </div>
            </fieldset>
          })}
          {report.source_type === 'live' && <label className="check"><input type="checkbox" checked={release}
            onChange={e => setRelease(e.target.checked)} />向学生发布此版本</label>}
          <button disabled={busy}>保存本地成果评价</button>
        </form>
      </>}
    </div>
  </details>
}
