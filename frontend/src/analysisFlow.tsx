import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { api, message, terminal } from './api'
import type { Job, StudyChatQuality } from './api'
import { Badge, Busy, Empty, ErrorNotice, coverageNames, processDisplay, statusNames, useData, useJob } from './ui'

type Source = { id: string, name: string, session_count: number }
type Builtin = { sessions: number, stats: Record<string, number>, source_id: string | null }
type Option = { id: string, title: string, term: string, assignment: string, participant: string | null, deepseek_authorized: boolean }
type Checked = {
  checked_scope: string, count: number, turns: number, messages: number, grade_matches: number, submission_matches: number,
  expected_calls: number, maximum_calls: number, missing: Record<string, number>, coverage: Record<string, number>,
  studychat: boolean, eligible_count: number, complete_count: number, relevant_count: number, rich_count: number,
  blocked_reason: string | null, valid_grade_links: number,
  sessions: { session_id: string, title: string, turns: number, grade_matched: boolean, submission_matched: boolean,
    quality: StudyChatQuality | null }[],
}
export const qualityNames: Record<string, string> = {
  eligible: '合格', incomplete: '记录不完整', off_task: '偏离题目', too_short: '有效交互不足', uncertain: '关联待确认',
  relevant: '与作业相关', irrelevant: '与作业无关',
}
const steps = ['选择数据源', '选择分析范围', '导入并检查', '开始分析', '查看结果']
const stages: Record<string, string> = {
  dialogue: '正在整理完整对话', task_relevance: 'DeepSeek 正在核对作业相关性', evaluation: '正在执行五维分析',
  evidence: '正在检查证据引用', recommendations: '正在生成教学建议',
}

export function AnalysisFlow({ sources, builtin, showcaseIds, importSource }: {
  sources: Source[], builtin?: Builtin, showcaseIds: string[],
  importSource: (adapter: 'studychat_builtin' | 'generic_json', done: (id: string) => void) => ReactNode,
}) {
  const [step, setStep] = useState(1)
  const [kind, setKind] = useState<'studychat' | 'file' | 'existing'>('studychat')
  const [source, setSource] = useState('')
  const [term, setTerm] = useState('')
  const [assignment, setAssignment] = useState('')
  const [limit, setLimit] = useState(3)
  const [selected, setSelected] = useState<string[]>([])
  const [search, setSearch] = useState('')
  const [visibleLimit, setVisibleLimit] = useState(40)
  const [checked, setChecked] = useState<Checked>()
  const [confirmed, setConfirmed] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const requestKey = useRef('')
  const options = useData<{ items: Option[], studychat: boolean, deepseek_available: boolean, deepseek_reason: string }>(source ? `/datasets/${source}/analysis-options` : null)
  const { job, error: jobError } = useJob(jobId)
  const choices = options.data?.items ?? []
  const model = options.data?.studychat ? 'deepseek-flash' : 'rules'
  const needle = search.trim().toLowerCase()
  const focused = kind === 'studychat' && !needle && showcaseIds.length > 0
  const original = useData<{ session_id: string | null }>(source && options.data?.studychat &&
    /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(needle) ? `/datasets/${source}/studychat-chat/${needle}` : null)
  const visible = choices.filter(item => (!term || item.term === term) && (!assignment || item.assignment === assignment) &&
    (!focused || showcaseIds.includes(item.id)) &&
    (original.data?.session_id === item.id || [item.id, item.participant ?? '', item.title].some(text => text.toLowerCase().includes(needle))))
  const terms = [...new Set(choices.map(item => item.term).filter(Boolean))]
  const assignments = [...new Set(choices.filter(item => !term || item.term === term).map(item => item.assignment))]
  const payload = { source_id: source, session_ids: selected, model, max_count: limit, term, assignment }

  useEffect(() => { if (job && terminal(job.status)) setStep(5) }, [job?.status])

  function invalidate() {
    setChecked(undefined); setConfirmed(false); setError(''); requestKey.current = ''
  }
  function chooseSource(id: string) {
    setSource(id); setTerm(''); setAssignment(''); setSearch(''); setVisibleLimit(40); setSelected([]); invalidate()
  }
  function nextFromSource() {
    if (kind === 'studychat') chooseSource(builtin?.source_id ?? '')
    if (kind === 'file') chooseSource('')
    setStep(2)
  }
  async function inspect(nextStep = step) {
    setStep(nextStep); setBusy(true); invalidate()
    try { setChecked(await api<Checked>('/analysis/inspect', 'POST', payload)) }
    catch (e) { setError(message(e)) } finally { setBusy(false) }
  }
  async function start() {
    if (!checked || busy) return
    setBusy(true); setError('')
    requestKey.current ||= crypto.randomUUID()
    try {
      const run = await api<Job>('/analysis/start', 'POST', { ...payload, checked_scope: checked.checked_scope,
        request_key: requestKey.current, confirm_model_scope: confirmed })
      setJobId(run.id); setStep(4)
    } catch (e) { setError(message(e)) } finally { setBusy(false) }
  }

  const scopeFields = <div className="analysis-scope stack">
    <div className="form-grid">
      <label>学期<select aria-label="学期" value={term} disabled={!terms.length || busy} onChange={e => {
        setTerm(e.target.value); setAssignment(''); setSelected([]); setVisibleLimit(40); invalidate()
      }}><option value="">全部学期</option>{terms.map(value => <option key={value}>{value}</option>)}</select></label>
      <label>作业<select aria-label="作业" value={assignment} disabled={!assignments.length || busy} onChange={e => {
        setAssignment(e.target.value); setSelected([]); setVisibleLimit(40); invalidate()
      }}><option value="">全部作业</option>{assignments.map(value => <option key={value}>{value}</option>)}</select></label>
      <label>最多分析数量<select aria-label="最多分析数量" value={limit} disabled={busy} onChange={e => {
        const value = Number(e.target.value); setLimit(value); setSelected(old => old.slice(0,value)); invalidate()
      }}>{[1,2,3,4,5].map(value => <option key={value} value={value}>{value} 条</option>)}</select></label>
      <label>评价模型<select aria-label="评价模型" value={model} disabled>
        <option value="rules">本地规则</option><option value="deepseek-flash">DeepSeek Flash 1017</option></select></label>
    </div>
    <p className="muted">{options.data?.deepseek_reason ?? '导入后可核查实际可用范围与模型。'}</p>
    {options.data?.studychat && <p className="notice">先在本机扫描数据质量。通过后，才可由管理员确认并调用 DeepSeek。</p>}
    <ErrorNotice text={options.error} />{source && options.loading && <Busy />}
    {!!source && options.data && <>
      <div className="row"><strong>具体会话</strong><span className="muted">找到 {visible.length} 条 · 已选 {selected.length}/{limit}</span></div>
      {focused && <p className="muted">默认显示课堂精选案例。可按会话号或原始 chatId 查找其他归档。</p>}
      <label>搜索会话<input value={search} placeholder="原始 chatId、会话编号、匿名学生或作业名称" onChange={e => { setSearch(e.target.value); setVisibleLimit(40) }} /></label>
      {original.loading && <Busy />}
      {visible.length === 0 ? <Empty>筛选范围内没有可选会话。</Empty> : <div className="analysis-session-list" aria-label="具体会话" tabIndex={0}>
        {visible.slice(0,visibleLimit).map(item => <label className="check" key={item.id}>
          <input type="checkbox" aria-label={`选择会话 ${item.title} ${item.participant ?? item.id.slice(0,8)}`}
            checked={selected.includes(item.id)} disabled={busy || selected.length >= limit && !selected.includes(item.id)}
            onChange={e => { setSelected(old => e.target.checked ? [...old,item.id] : old.filter(id => id !== item.id)); invalidate() }} />
          <span><strong>{item.title}</strong><small>{item.participant || item.id.slice(0,8)}</small></span>
          {item.deepseek_authorized && <Badge>已逐条授权</Badge>}
        </label>)}
      </div>}
      {visible.length > visibleLimit && <button className="secondary" onClick={() => setVisibleLimit(old => old + 40)}>显示更多会话</button>}
    </>}
  </div>

  return <section className="panel stack analysis-flow" aria-label="现场分析流程">
    <ol className="analysis-steps">{steps.map((label, i) => <li className={i + 1 === step ? 'active' : i + 1 < step ? 'complete' : ''}
      key={label} aria-current={i + 1 === step ? 'step' : undefined}><span>{i + 1}</span>{label}</li>)}</ol>
    <ErrorNotice text={error || jobError} />

    {step === 1 && <div className="stack">
      <h2>选择数据源</h2>
      <div className="analysis-source-grid">
        <label className="analysis-source-card"><input type="radio" name="analysis-source" checked={kind === 'studychat'} onChange={() => setKind('studychat')} />
          <h3>StudyChat v1.1 · 内置数据集</h3><p>{builtin ? `${builtin.sessions} 条会话 · ${builtin.stats.interactions} 条历史交互` : '读取本地快照目录'}</p></label>
        <label className="analysis-source-card"><input type="radio" name="analysis-source" checked={kind === 'file'} onChange={() => setKind('file')} />
          <h3>通用 JSON / JSONL</h3><p>上传对话文件，检查并规范化事件。</p></label>
        <label className="analysis-source-card"><input type="radio" name="analysis-source" checked={kind === 'existing'} onChange={() => setKind('existing')} />
          <h3>已导入数据源</h3><p>直接使用当前能评价的数据源。</p></label>
      </div>
      {kind === 'existing' && <label>已导入来源<select value={source} onChange={e => chooseSource(e.target.value)}>
        <option value="">选择数据源</option>{sources.map(item => <option value={item.id} key={item.id}>{item.name} · {item.session_count} 条</option>)}</select></label>}
      <button disabled={kind === 'existing' && !source || kind === 'studychat' && !builtin} onClick={nextFromSource}>下一步 · 选择范围</button>
    </div>}

    {step === 2 && <div className="stack"><h2>选择分析范围</h2>{scopeFields}
      {!source && <p className="muted">先设置数量上限，导入并规范化后选择具体会话。</p>}
      <div className="toolbar"><button className="secondary" onClick={() => setStep(1)}>返回选择来源</button>
        <button disabled={busy || !!source && (!selected.length || options.loading)} onClick={() => source ? void inspect(3) : setStep(3)}>
          {options.data?.studychat ? '检查数据质量' : '导入并检查'}</button></div>
    </div>}

    {step === 3 && <div className="stack"><h2>{options.data?.studychat ? '数据质量检查' : '导入并检查'}</h2>
      {!source && importSource(kind === 'file' ? 'generic_json' : 'studychat_builtin', chooseSource)}
      {!!source && !checked && <>{scopeFields}<button disabled={busy || !selected.length} onClick={() => void inspect()}>检查所选会话</button></>}
      {busy && <Busy />}
      {checked && <>
        <div className="analysis-data-metrics">
          <div><strong>{checked.count}</strong>会话</div>
          <div><strong>{checked.turns}</strong>{checked.studychat ? '实际对话轮次' : '对话轮次'}</div>
          <div><strong>{checked.grade_matches}</strong>{checked.studychat ? '档案有成绩' : '成绩匹配'}</div>
          <div><strong>{checked.submission_matches}</strong>提交物匹配</div>
        </div>
        <div className="chips"><Badge>{checked.messages} 条原始消息</Badge>{Object.entries(checked.coverage).map(([key,count]) =>
          <Badge key={key}>{coverageNames[key] ?? key} {count}</Badge>)}</div>
        <ul className="plain" aria-label="数据缺失情况">{Object.entries(checked.missing).map(([label,count]) => <li key={label}>{label} · {count} 条</li>)}</ul>
        {!Object.keys(checked.missing).length && <p className="muted">本批所列字段均已匹配。</p>}
        {checked.studychat && <>
          <div className="chips" aria-label="资格检查汇总"><Badge>完整 {checked.complete_count}</Badge>
            <Badge>题目相关 {checked.relevant_count}</Badge><Badge>有评价线索 {checked.rich_count}</Badge>
            <Badge>有效成绩关联 {checked.valid_grade_links}</Badge>
            <Badge>最终可评价 {checked.eligible_count}</Badge></div>
          <ul className="plain analysis-run-list">{checked.sessions.map(item => item.quality && <li key={item.session_id}>
            <div><strong>{item.title} · {item.session_id.slice(0, 8)}</strong>
              <small>{item.quality.eligibility_status === 'eligible' ? '纳入本次评价' :
                item.quality.exclusion_reasons.map(reason => qualityNames[reason]).join(' · ')}</small>
              <small>实际 {item.quality.observed_turns} 轮 · 声明 {item.quality.declared_turns ?? '未知'} 轮 · {qualityNames[item.quality.task_relevance]}</small>
              {item.quality.eligibility_status === 'eligible' && <small>纳入 {item.quality.scoped_turns} 轮符合检查的原始对话。</small>}
              <small>{item.quality.relevance_reason}</small>
              {item.quality.grade_matched && !item.quality.grade_link_valid && <small>档案有成绩，当前对话无法支持成绩关联。</small>}</div>
            <a href={`#/session/${item.session_id}`}>查看原始会话</a>
          </li>)}</ul>
          {checked.eligible_count === 0 && <p className="notice">没有通过质量检查的会话。</p>}
          {checked.blocked_reason && <p className="notice">{checked.blocked_reason}</p>}
        </>}
        <div className="notice" role="status">预计模型调用 <b>{checked.blocked_reason ? 0 : checked.expected_calls}</b> 次{checked.blocked_reason ? ' · 模型启动受阻' : checked.maximum_calls > 0 ?
          ` · 含分段和校验重试上限 ${checked.maximum_calls} 次` : ' · 完全本地处理'}</div>
        {checked.studychat && !checked.blocked_reason && checked.eligible_count > 0 &&
          <label className="check"><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} />
          确认调用已授权且通过检查的会话。发送脱敏题目、合格对话、证据编号和必要提交物摘要。</label>}
        <button disabled={busy || checked.studychat && (!checked.eligible_count || !!checked.blocked_reason || !confirmed)}
          onClick={() => void start()}>{checked.studychat ? '使用 DeepSeek 生成五维评价' : '开始分析'}</button>
      </>}
      <button className="secondary" disabled={busy} onClick={() => { invalidate(); setStep(2) }}>返回调整范围</button>
    </div>}

    {step >= 4 && <div className="stack" aria-live="polite">
      <h2>{step === 4 ? '现场分析进度' : '本次新生成的报告'}</h2>
      {!job && <Busy />}
      {job && <>
        <div className="analysis-job-line"><strong>{step === 5 && job.status === 'completed' ? '运行结束' : statusNames[job.status] || job.status}</strong>
          <span>已生成报告 {job.succeeded_count} · 失败 {job.failed_count} · 共 {job.progress_total} 条</span></div>
        <progress value={job.progress_done} max={job.progress_total || 1} aria-label="已处理会话" />
        {job.analysis && <small className="muted">实际模型请求 {job.analysis.provider_calls_started} 次 · 本次上限 {job.analysis.provider_call_limit} 次</small>}
        <ul className="plain analysis-run-list">{job.runs?.map(run => <li key={run.id}>
          <div><strong>{run.task_title}</strong><small>{run.session_id.slice(0, 8)}</small>
            {run.report_id && <small>{run.provider === 'rules' ? '本地规则' : run.provider === 'deepseek' ? 'DeepSeek' : '评价报告'}
              {' · '}{run.process_score != null ? `过程分 ${processDisplay(run.process_score, run.process_score_five)}`
                : run.scored_dimensions != null ? `${run.scored_dimensions} / 5 维可评分 · 未形成过程总分` : '未形成过程总分'}</small>}</div>
          {job.analysis?.model === 'deepseek-flash' && <small>deepseek / deepseek-flash · 调用 {run.actual_model_calls ?? 0} 次
            {run.segment_count != null && ` · ${run.segment_count} 个分片`} · {run.provider_response_valid ? '真实返回已校验' :
              run.status === 'failed' ? '未取得通过校验的返回' : '等待有效返回'}</small>}
          {run.report_id ? <a href={options.data?.studychat ? `#/session/${run.session_id}/${run.id}/${run.report_id}` : `#/report/${run.id}/${run.report_id}`}>查看本次报告</a>
            : <span>{run.status === 'queued' && run.stage ? stages[run.stage] : statusNames[run.status]}</span>}
          {run.error && <ErrorNotice text={JSON.parse(run.error).message} />}
        </li>)}</ul>
        {step === 5 && job.succeeded_count === 0 && <Empty>本次没有成功生成报告。可以调整范围后重新分析。</Empty>}
        {step === 5 && <button className="secondary" onClick={() => {
          setJobId(null); requestKey.current = ''; setConfirmed(false); setStep(3)
        }}>重新分析</button>}
      </>}
    </div>}
  </section>
}
