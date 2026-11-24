import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { ApiError, api, message, terminal } from './api'
import type { Dimension, Job, Page, Session } from './api'
import type { Task } from './live'
import { Badge, Busy, Empty, ErrorNotice, Heading, JobCard, Pager, coverageNames, dimensionNames, fivePointRubric, outcomeName, processDisplay,
  fiveDimensionNames, legacyDimensionNames,
  methodNames, statusNames, time, useData, useJob } from './ui'
import { ExportCard } from './analysis'
import { AnalysisFlow, qualityNames } from './analysisFlow'
import { ShowcaseLibrary } from './showcase'
import type { EntryMode, EntrySettings, ShowcaseSummary } from './showcase'

type Source = { id: string, name: string, adapter: string, session_count: number, created_at: string, license_note: string,
  completeness: { coverage: string, missing: string[] }[] }
type DatasetSource = Source & {
  demo: number, can_export: boolean, can_evaluate: boolean, can_delete: boolean, studychat_snapshot: boolean,
  deepseek_consented: boolean, deepseek_scope: string, can_authorize_deepseek: boolean,
}
type Preview = {
  id: string, source: string, count: number, valid: number, coverage: Record<string, number>,
  examples: { participant: string, task: string, events: number, coverage: string, missing: string[], custom: number }[],
  errors: { record: number, message: string }[],
  task_conflicts?: number, mapped_task?: { title: string, version: string },
  snapshot?: { version: string, files: string[], stats: Record<string, number>, reports_run_log_present: boolean },
}

export function Datasets({ initialMode }: { initialMode?: EntryMode }) {
  const [version, reload] = useState(0)
  const [page, setPage] = useState(1)
  const sources = useData<Page<Source>>('/datasets?page=' + page, version)
  const builtin = useData<{ sessions: number, stats: Record<string, number>, source_id: string | null }>('/datasets/builtins/studychat', version)
  const entries = useData<EntrySettings>('/studychat/entry-settings')
  const showcase = useData<{ items: ShowcaseSummary[] }>('/studychat/showcase', version)
  const [modeChoice, setModeChoice] = useState<EntryMode | undefined>(initialMode)
  const mode = entries.data && (modeChoice && entries.data[`${modeChoice}_enabled`] ? modeChoice : entries.data.default_mode)
  const cases = showcase.data?.items ?? []
  return <>
    <Heading title="数据集评价">StudyChat 课堂回放与现场分析</Heading>
    <ErrorNotice text={entries.error || showcase.error || builtin.error} />
    {entries.loading && <Busy />}
    {entries.data && <div className="toolbar showcase-entries" aria-label="StudyChat 入口">
      {entries.data.replay_enabled && <button type="button" className={mode === 'replay' ? '' : 'secondary'}
        aria-pressed={mode === 'replay'} onClick={() => setModeChoice('replay')}>课堂回放</button>}
      {entries.data.live_enabled && <button type="button" className={mode === 'live' ? '' : 'secondary'}
        aria-pressed={mode === 'live'} onClick={() => setModeChoice('live')}>现场分析</button>}
    </div>}
    {mode === 'replay' && (showcase.loading ? <Busy /> : <ShowcaseLibrary cases={cases} />)}
    {mode === 'live' && <>
    <AnalysisFlow sources={sources.data?.items ?? []} builtin={builtin.data}
      showcaseIds={cases.flatMap(item => item.session_id ? [item.session_id] : [])} importSource={(adapter, complete) =>
      <ImportForm lockedAdapter={adapter} onComplete={id => { complete(id); reload(old => old + 1) }} />} />
    <details className="panel session-task"><summary>数据源管理与兼容导入</summary><div className="grid two align-start divider">
      <section className="panel"><h2>数据源</h2>
        <ErrorNotice text={sources.error} />
        {sources.loading && <Busy />}
        {sources.data?.items.length === 0 && <Empty>尚无数据源。可从右侧加载演示集。</Empty>}
        <div className="stack">{sources.data?.items.map(item => <a className="list-card" href={'#/datasets/' + item.id} key={item.id}>
          <span className="row"><strong>{item.name}</strong><Badge>{item.adapter}</Badge></span>
          <span className="muted">{item.session_count} 条会话 · {time(item.created_at)}</span>
          <span>{item.license_note}</span>
          <span className="muted">{[...new Set(item.completeness.map(row => coverageNames[row.coverage]))].join(' · ')}</span>
        </a>)}</div>
        {sources.data && <Pager page={page} total={sources.data.total} size={20} set={setPage} />}
      </section>
      <ImportForm onComplete={() => reload(old => old + 1)} />
    </div></details>
    </>}
  </>
}

function ImportForm({ onComplete, lockedAdapter }: { onComplete: (sourceId: string) => void, lockedAdapter?: 'studychat_builtin' | 'generic_json' }) {
  const [adapter, setAdapter] = useState<string>(lockedAdapter ?? 'studychat_builtin')
  const [preview, setPreview] = useState<Preview>()
  const [jobId, setJobId] = useState<string | null>(null)
  const [previewJobId, setPreviewJobId] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [license, setLicense] = useState(false)
  const [onlyValid, setOnlyValid] = useState(false)
  const [headers, setHeaders] = useState<string[]>([])
  const { job, error: pollError } = useJob(jobId)
  const previewJob = useJob(previewJobId)
  const batchId = previewJob.job?.status === 'completed' ? previewJob.job.result?.batch_id : undefined
  const localPreview = useData<Preview>(batchId ? '/datasets/imports/' + batchId + '/preview' : null)
  const taskOptions = useData<{ items: Task[] }>(['studychat', 'generic_json'].includes(adapter) ? '/task-definitions' : null)
  useEffect(() => { if (localPreview.data) setPreview(localPreview.data) }, [localPreview.data])
  const completeId = job?.status === 'completed' ? job.result?.source_id : undefined
  useEffect(() => { if (completeId) onComplete(completeId) }, [completeId])

  async function inspect(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const metadata: Record<string, unknown> = Object.fromEntries(['name', 'source_identity', 'license_note', 'intended_use', 'retain_until', 'mapped_task_id']
      .map(key => [key, form.get(key)]).filter(([, value]) => value))
    setBusy(true); setError(''); setPreview(undefined); setJobId(null); setPreviewJobId(null); setLicense(false); setOnlyValid(false); setHeaders([])
    try {
      if (adapter === 'studychat_local' || adapter === 'studychat_builtin') {
        const queued = await api<Job>('/datasets/imports/' + (adapter === 'studychat_builtin' ? 'studychat-builtin' : 'studychat-local'), 'POST',
          adapter === 'studychat_builtin' ? metadata : { ...metadata, semester: form.get('semester'), assignment: form.get('assignment') })
        setPreviewJobId(queued.id)
        return
      }
      metadata.allow_external_model = form.get('allow_external_model') === 'on'
      const mapping = form.get('mapping')
      if (mapping) {
        try { metadata.mapping = JSON.parse(String(mapping)) } catch { throw new Error('字段映射需要有效的 JSON。') }
      }
      const payload = new FormData()
      payload.set('adapter', adapter)
      payload.set('metadata', JSON.stringify(metadata))
      const file = form.get('file')
      if (file instanceof File && file.size > 0) payload.set('file', file)
      setPreview(await api<Preview>('/datasets/imports', 'POST', payload))
    } catch (e) {
      setError(message(e))
      if (e instanceof ApiError && Array.isArray(e.details.headers)) setHeaders(e.details.headers.map(String))
    } finally { setBusy(false) }
  }
  async function commit() {
    if (!preview) return
    setBusy(true); setError('')
    try {
      const next = await api<Job>('/datasets/imports/' + preview.id + '/commit', 'POST',
        { license_confirmed: license, only_valid: onlyValid })
      setJobId(next.id)
    } catch (e) { setError(message(e)) } finally { setBusy(false) }
  }
  return <section className="panel" id="studychat-import">
    <h2>导入向导</h2>
    <form className="stack" onSubmit={inspect}>
      {!lockedAdapter && <label>适配器<select value={adapter} onChange={e => { setAdapter(e.target.value); setPreview(undefined); setJobId(null); setPreviewJobId(null); setError('') }}>
        <option value="studychat_builtin">StudyChat v1.1 内置数据集</option>
        <option value="studychat_local">StudyChat v1.1 本地快照</option>
        <option value="demo">内置演示集</option><option value="generic_json">通用 JSON / JSONL</option><option value="studychat">StudyChat CSV 映射</option>
      </select></label>}
      {adapter === 'demo' ? <p className="muted">六条校园商店任务记录，包含未运行即提交、重复报错和纠正 AI 建议等案例。</p> : <>
        {adapter === 'studychat_builtin' ? <p className="muted">已随系统放入标准化快照。首次使用先预览并确认 CC BY 4.0 来源许可。</p>
        : adapter === 'studychat_local' ? <>
          <p className="muted">从项目的固定 StudyChat 目录读取对话、题目、已同意公开的成绩及可关联提交。按学生和作业合并会话，保留对话轮次。</p>
          <div className="form-grid"><label>学期<select name="semester"><option value="all">两个学期</option>
            <option value="f24">F24</option><option value="s25">S25</option></select></label>
            <label>作业<select name="assignment"><option value="all">全部</option>
              {Array.from({ length: 7 }, (_, i) => 'a' + (i + 1)).map(key => <option value={key} key={key}>{key.toUpperCase()}</option>)}
            </select></label></div>
        </> : <>
          <label>上传文件<input name="file" type="file" required accept={adapter === 'studychat' ? '.csv' : '.json,.jsonl'} /></label>
          <label>稳定来源标识<input name="source_identity" maxLength={100} required placeholder="course-2026" /></label>
          <label>关联已发布任务<select name="mapped_task_id"><option value="">保留来源题目快照</option>
            {taskOptions.data?.items.filter(task => task.status === 'published').map(task => <option key={task.id} value={task.id}>{task.title} · {task.version}</option>)}
          </select></label>
          <label className="check"><input type="checkbox" name="allow_external_model" />许可允许向已审批的外部模型发送最少必要证据</label>
        </>}
        {adapter !== 'studychat_builtin' && <label>数据源名称<input name="name" maxLength={160} placeholder="未填写时使用来源名称" required={adapter === 'studychat'} /></label>}
        <label>用途<input key={adapter} name="intended_use" maxLength={1000}
          defaultValue={adapter === 'studychat_builtin' ? '本地课程评价方法验证' : ''} required /></label>
        {adapter !== 'studychat_builtin' && <label>许可说明<input name="license_note" maxLength={1000} required placeholder={adapter === 'studychat_local' ? '如 CC BY 4.0，保留出处与使用范围' : ''} /></label>}
        <label>保留期限<input key={'retention-' + adapter} name="retain_until" type="date"
          defaultValue={adapter === 'studychat_builtin' ? new Date(Date.now() + 180 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10) : ''} required /></label>
        {adapter === 'studychat' && <label>字段映射 JSON<textarea name="mapping" rows={3}
          placeholder={'{"participant":"student","task":"assignment","role":"role","text":"message","session":"session"}'} /></label>}
      </>}
      <button disabled={busy || !!job && !terminal(job.status) || !!previewJobId && (!previewJob.job || !terminal(previewJob.job.status))}>
        {busy ? '处理中…' : adapter === 'demo' ? '加载 Demo 预览' : adapter === 'studychat_builtin' ? '加载内置 StudyChat 预览'
          : adapter === 'studychat_local' ? '扫描本地快照并预览' : '检查文件并预览'}</button>
    </form>
    <ErrorNotice text={error || pollError || previewJob.error || localPreview.error} />
    {previewJobId && <JobCard job={previewJob.job} />}
    {headers.length > 0 && <div className="notice">识别到的列：{headers.join('、')}</div>}
    {preview && <div className="divider stack">
      <h3>导入预览 · {preview.source}</h3>
      <p>{preview.valid} / {preview.count} 条会话通过校验。</p>
      {preview.snapshot && <div className="notice stack">
        <p>{preview.snapshot.stats.interactions || 0} 条交互 · {preview.snapshot.stats.sessions_with_grades || 0} 条会话有关联成绩 · {preview.snapshot.stats.sessions_with_submissions || 0} 条会话有关联作业归档。</p>
        <p>缺少可核验的运行日志。来源成绩保留为对照字段，外部模型默认关闭。</p>
        <details><summary>读取的文件</summary><ul className="plain">{preview.snapshot.files.map(file => <li key={file}>{file}</li>)}</ul></details>
      </div>}
      {!!preview.task_conflicts && <Badge tone="warn">{preview.task_conflicts} 个相同任务标识包含不同题目文本，已保留独立快照</Badge>}
      {preview.mapped_task && <Badge>{preview.mapped_task.title} · {preview.mapped_task.version}</Badge>}
      <div className="chips">{Object.entries(preview.coverage).map(([key, count]) => <Badge key={key}>{coverageNames[key]} {count}</Badge>)}</div>
      <ul className="plain">{preview.examples.map((item, i) => <li key={i}>
        <b>{item.participant}</b> · {item.events} 条事件 · {coverageNames[item.coverage]}
        {item.missing.length > 0 && <div className="muted">缺少 {item.missing.join('、')}</div>}
        {item.custom > 0 && <div className="muted">{item.custom} 条未知类型事件仅保留展示</div>}
      </li>)}</ul>
      {preview.errors.length > 0 && <><ul>{preview.errors.map(item => <li key={item.record}>第 {item.record} 条 · {item.message}</li>)}</ul>
        <label className="check"><input type="checkbox" checked={onlyValid} onChange={e => setOnlyValid(e.target.checked)} />只导入有效记录</label></>}
      <label className="check"><input type="checkbox" checked={license} onChange={e => setLicense(e.target.checked)} />我确认有权按所填用途导入这些记录</label>
      <button disabled={!license || !preview.valid || busy || !!jobId || !!preview.errors.length && !onlyValid} onClick={commit}>确认导入</button>
      <JobCard job={job} />
      {completeId && !lockedAdapter && <a className="button secondary" href={'#/datasets/' + completeId}>打开数据源</a>}
    </div>}
  </section>
}

export function Dataset({ id }: { id: string }) {
  const [page, setPage] = useState(1)
  const [task, setTask] = useState('')
  const [participant, setParticipant] = useState('')
  const [coverage, setCoverage] = useState('')
  const [since, setSince] = useState('')
  const [until, setUntil] = useState('')
  const params = new URLSearchParams({ source_id: id, page: String(page), task, participant, coverage, since, until })
  const list = useData<Page<Session>>('/sessions?' + params)
  const source = useData<DatasetSource>('/datasets/' + id)
  const [selected, setSelected] = useState<string[]>([])
  const [method, setMethod] = useState('hybrid')
  const [jobId, setJobId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const { job, error: pollError } = useJob(jobId)
  const [deletion, setDeletion] = useState<{ preview_token: string, counts: Record<string, number> }>()
  const visibleIds = list.data?.items.map(item => item.id) || []
  const visibleSelected = selected.filter(id => visibleIds.includes(id))
  const chosenMethod = source.data?.studychat_snapshot ? 'rules' : method

  async function evaluate() {
    setBusy(true); setError('')
    try {
      const next = await api<Job>('/evaluation-runs/batch', 'POST', { source_id: id, session_ids: visibleSelected, method: chosenMethod })
      setJobId(next.id)
    } catch (e) { setError(message(e)) } finally { setBusy(false) }
  }
  async function previewDeletion() {
    setError('')
    try { setDeletion(await api('/datasets/' + id + '/deletion-preview')) }
    catch (e) { setError(message(e)) }
  }
  async function deleteSource() {
    if (!deletion) return
    setError('')
    try {
      await api('/datasets/' + id, 'DELETE', { expected_preview: deletion.preview_token })
      location.hash = '#/datasets'
    } catch (e) { setError(message(e)) }
  }
  return <>
    <Heading title="会话浏览器" actions={<a className="button secondary" href="#/datasets">返回数据集</a>}>
      {source.data?.name || '数据源'} · 选择会话和评价方法。已完成的报告也可以从会话详情打开。
    </Heading>
    <div className="stack">
    {source.data?.studychat_snapshot && <section className="panel stack">
      <h2>StudyChat 现场评价</h2>
      <a href="#/datasets">前往数据质量检查</a>
      <ExcludedReports id={id} />
    </section>}
    <section className="panel stack">
      <div className="form-grid">
        <label>任务名称<input value={task} onChange={e => { setTask(e.target.value); setPage(1) }} /></label>
        <label>参与者假名<input value={participant} onChange={e => { setParticipant(e.target.value); setPage(1) }} /></label>
        <label>可评价范围<select value={coverage} onChange={e => { setCoverage(e.target.value); setPage(1) }}>
          <option value="">全部</option>{Object.entries(coverageNames).map(([key, value]) => <option key={key} value={key}>{value}</option>)}
        </select></label>
        <label>导入开始日期<input type="date" value={since} onChange={e => { setSince(e.target.value); setPage(1) }} /></label>
        <label>导入结束日期<input type="date" value={until} onChange={e => { setUntil(e.target.value); setPage(1) }} /></label>
      </div>
      <ErrorNotice text={list.error || error || pollError} />
      {list.loading && <Busy />}
      {list.data?.items.length === 0 && <Empty>没有符合条件的会话。</Empty>}
      {!!list.data?.items.length && <>
        <div className="table-wrap"><table><thead><tr>
          <th><label className="check"><input type="checkbox" aria-label="选择当前页" checked={visibleSelected.length === visibleIds.length}
            onChange={e => setSelected(e.target.checked ? visibleIds : [])} />选择</label></th>
          <th>任务</th><th>参与者假名</th><th>可评价范围</th>
        </tr></thead><tbody>{list.data.items.map(item => <tr key={item.id}>
          <td><input type="checkbox" aria-label={'选择 ' + (item.fixture_id || item.participant)} checked={selected.includes(item.id)}
            onChange={e => setSelected(old => e.target.checked ? [...old, item.id] : old.filter(id => id !== item.id))} /></td>
          <td><a href={'#/session/' + item.id}>{item.task_title}{item.fixture_id && ' · ' + item.fixture_id}</a></td>
          <td>{item.participant}</td><td>{coverageNames[item.coverage || 'none']}</td>
        </tr>)}</tbody></table></div>
        <Pager page={page} total={list.data.total} size={20} set={setPage} />
        {source.data?.can_evaluate && !source.data.studychat_snapshot && <div className="toolbar">
          <label>评价方法<select aria-label="评价方法" value={chosenMethod} onChange={e => setMethod(e.target.value)}>
            {Object.entries(methodNames).filter(([key]) => !source.data?.studychat_snapshot || key === 'rules')
              .map(([key, label]) => <option value={key} key={key} disabled={key === 'demo_conflict' && !list.data?.items.some(item => item.fixture_id === 'F5')}>{label}</option>)}
          </select></label>
          <button disabled={!visibleSelected.length || busy || !!job && !terminal(job.status)} onClick={evaluate}>评价选中的 {visibleSelected.length} 条</button>
        </div>}
      </>}
      <JobCard job={job} />
    </section>
    <DatasetResults id={id} studychat={source.data?.studychat_snapshot} refresh={job?.status === 'completed' ? 1 : 0} />
    {source.data?.can_export && <section className="panel stack"><h2>数据源导出</h2><ExportCard id={id} demo={!!source.data.demo} /></section>}
    {source.data?.can_delete && <details className="panel">
      <summary>删除数据源</summary><div className="stack divider">
        <button className="secondary" onClick={() => void previewDeletion()}>检查删除影响</button>
        {deletion && <><p>将删除 {deletion.counts.sessions} 条会话、{deletion.counts.events} 条事件、
          {deletion.counts.reports} 个报告版本和 {deletion.counts.exports} 个导出。</p>
          <button onClick={() => void deleteSource()}>确认删除此数据源</button></>}
      </div>
    </details>}
    </div>
  </>
}

type ResultRow = {
  id: string, run_id: string, session_id: string, method: string, process_score: number | null, outcome_score: number | null,
  process_score_five: number | null,
  outcome_basis: 'source_grade' | 'system_rubric' | 'local_review' | null,
  rubric_version: string, legacy_rubric: boolean,
  diagnosis: string | null, reviewed: boolean, dimensions: Pick<Dimension, 'dimension' | 'state' | 'level'>[],
}

function ExcludedReports({ id }: { id: string }) {
  const [page, setPage] = useState(1)
  const records = useData<Page<{ report_id: string, session_id: string, title: string, reason: string[] }>>(
    `/datasets/${id}/excluded-reports?page=${page}`)
  return <details><summary>被排除记录{records.data && ` · ${records.data.total}`}</summary>
    <ErrorNotice text={records.error} />
    {records.loading && <Busy />}
    {records.data?.total === 0 && <Empty>当前没有被排除的报告。</Empty>}
    <ul>{records.data?.items.map(row => <li key={row.report_id}>
      <a href={`#/session/${row.session_id}`}>{row.title} · {row.session_id.slice(0, 8)}</a>
      {' · '}{row.reason.map(reason => qualityNames[reason] ?? reason).join(' · ')}
    </li>)}</ul>
    {records.data && <Pager page={page} total={records.data.total} size={records.data.page_size} set={setPage} />}
  </details>
}

function DatasetResults({ id, refresh, studychat }: { id: string, refresh: number, studychat?: boolean }) {
  const [page, setPage] = useState(1)
  const [filters, setFilters] = useState<Record<string, string>>({})
  const query = new URLSearchParams({ ...Object.fromEntries(Object.entries(filters).filter(([, val]) => val !== '')), page: String(page) })
  if (filters.process_min) query.set('process_min', String(Number(filters.process_min) * 20))
  const results = useData<Page<ResultRow>>('/datasets/' + id + '/results?' + query, refresh)
  const update = (key: string, val: string) => {
    setFilters(old => ({ ...old, ...(key === 'rubric' ? { dimension: '' } : {}), [key]: val })); setPage(1)
  }
  const selects: [string, string, [string, string][]][] = [
    ['method', '评价方法筛选', Object.entries(methodNames)],
    ['rubric', '量规版本', [['five', '五维量规'], ['legacy', '历史四维报告']]],
    ['dimension', '维度', Object.entries(filters.rubric === 'legacy' ? legacyDimensionNames : fiveDimensionNames)],
    ['level', '等级筛选', ['0', '1', '2', '3', '4', '5'].map(n => [n, n === '0' ? '0（历史量规）' : n])],
    ['state', '证据状态', ['scored', 'not_applicable', 'insufficient_evidence'].map(key => [key, statusNames[key]])],
    ['diagnosis', '二维诊断', [['none', '暂不形成'], ...['过程与成果均达标', '过程需加强、成果达标', '过程达标、成果需完善',
      '过程与成果均需支持'].map<[string, string]>(key => [key, key])]],
    ['reviewed', '复核筛选', [['true', '已复核'], ['false', '未复核']]],
  ]
  return <section className="panel stack"><h2>评价结果</h2>
    <div className="form-grid">{selects.map(([key, title, items]) => <label key={key}>{title}
      <select value={filters[key] || ''} onChange={event => update(key, event.target.value)}>
        <option value="">全部</option>{items.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select></label>)}
      <label>最低过程分（满分 5）<input type="number" min={0} max={5} step="0.01" value={filters.process_min || ''} onChange={event => update('process_min', event.target.value)} /></label>
      <label>最低有效成果分<input type="number" min={0} max={100} step="0.1" value={filters.outcome_min || ''} onChange={event => update('outcome_min', event.target.value)} /></label>
    </div>
    <ErrorNotice text={results.error} />{results.loading && <Busy />}
    {results.data?.items.length === 0 && <Empty>没有符合条件的评价结果。</Empty>}
    {results.data?.items.map(row => <article key={row.id} className="list-card">
      <div className="row"><a href={studychat ? `#/session/${row.session_id}/${row.run_id}/${row.id}` :
        `#/report/${row.run_id}/${row.id}`}>{methodNames[row.method]}报告</a>
        <Badge>{row.legacy_rubric ? '历史四维报告 · ' : ''}{row.reviewed ? '已复核' : '未复核'}</Badge></div>
      <div className="muted">过程 {processDisplay(row.process_score, row.process_score_five)}
        {row.outcome_score != null && ` · ${outcomeName(row.outcome_basis)} ${row.outcome_score}`}
        {row.diagnosis && ` · ${row.diagnosis}`}</div>
      <a href={'#/session/' + row.session_id}>打开会话详情</a>
      <div className="chips">{row.dimensions.map(item => <Badge key={item.dimension}>
        {dimensionNames[item.dimension]} {item.level == null ? statusNames[item.state] : `${item.level}/${fivePointRubric(row.rubric_version) ? 5 : 3}`}</Badge>)}</div>
    </article>)}
    {results.data && <Pager page={page} total={results.data.total} size={20} set={setPage} />}
  </section>
}
