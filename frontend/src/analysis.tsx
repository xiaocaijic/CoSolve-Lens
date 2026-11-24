import { useState } from 'react'
import { api, message, terminal } from './api'
import type { Job, Page } from './api'
import { Badge, Busy, Empty, ErrorNotice, Heading, JobCard, Pager, dimensionNames, fivePointRubric, methodNames, outcomeName, processDisplay, statusNames, useData, useJob } from './ui'

type Overview = {
  scope: string, report_count: number, sample_count: number, scorable_count: number, insufficient_count: number,
  methods: Record<string, number>, diagnoses: Record<string, number>, dimensions_by_rubric: Record<string, Record<string, Record<string, number>>>,
  process: number[], outcome: number[], external_assignments: number[],
}
type Results = {
  id: string, run_id: string, method: string, process_score: number | null, outcome_score: number | null,
  process_score_five: number | null,
  degraded: boolean, diagnosis: string | null, actual_method: string, external_scores: Record<string, number>,
  outcome_basis: 'source_grade' | 'system_rubric' | 'local_review' | null,
  system?: { process_score: number | null, process_score_five: number | null, outcome_score: number | null },
}
type Compare = { session_id: string, method_disagreement: boolean, teacher_disagreement: boolean, reports: Results[] }
type Source = { id: string, name: string, demo: number }

export function ExportCard({ id, demo = false, validation = false }: { id: string, demo?: boolean, validation?: boolean }) {
  const [format, setFormat] = useState('json')
  const [jobId, setJobId] = useState<string | null>(null)
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const job = useJob(jobId)
  async function exportData() {
    setBusy(true); setError('')
    try {
      const result = await api<Job>(validation ? '/analytics/validation-exports' : '/exports', 'POST', {
        source_id: id, format, include_demo: demo, acknowledge_scope: confirm,
      })
      setJobId(result.id)
    } catch (e) { setError(message(e)) } finally { setBusy(false) }
  }
  return <div className="stack">
    <p className="muted">默认保留规范化事件类型和测量结果、假名与评价值，省略对话和代码原文。</p>
    {demo && <Badge tone="warn">演示导出 · 排除在研究统计之外</Badge>}
    <div className="toolbar">
      {!validation && <label>导出格式<select value={format} onChange={e => setFormat(e.target.value)}>
        <option value="json">JSON</option><option value="csv">CSV</option>
      </select></label>}
      <label className="check"><input type="checkbox" checked={confirm} onChange={e => setConfirm(e.target.checked)} />我确认导出范围</label>
      <button disabled={!id || !confirm || busy || !!job.job && !terminal(job.job.status)} onClick={() => void exportData()}>生成导出</button>
    </div>
    <ErrorNotice text={error || job.error} /><JobCard job={job.job} />
    {job.job?.result?.export_id && <a href={'/api/exports/' + job.job.result.export_id + '/download'}>下载导出</a>}
  </div>
}

function Bars({ data, label }: { data: Record<string, number>, label?: (name: string) => string }) {
  const max = Math.max(1, ...Object.values(data))
  return <ul className="plain bars">{Object.entries(data).map(([name, count]) => <li key={name}>
    <span>{label ? label(name) : name}</span><progress max={max} value={count} aria-label={name} /><b>{count}</b>
  </li>)}</ul>
}

function distribution(values: number[], scale = 100) {
  return Object.fromEntries([[0, 25], [25, 50], [50, 75], [75, 101]].map(([min, max]) => [
    `${min * scale / 100}–${Math.min(max, 100) * scale / 100}${max === 101 ? '' : '（不含上界）'}`,
    values.filter(value => value >= min && value < max).length,
  ]))
}

export function Analytics() {
  const [scope, setScope] = useState('research')
  const [source, setSource] = useState('')
  const [cohort, setCohort] = useState('')
  const [page, setPage] = useState(1)
  const query = new URLSearchParams(cohort ? { scope, source_id: cohort } : { scope })
  const summary = useData<Overview>('/analytics/overview?' + query)
  const compare = useData<Page<Compare>>('/analytics/disagreements?' + query + '&page=' + page)
  const sources = useData<Page<Source>>('/datasets?page_size=100')
  const data = summary.data
  const ratio = (value: number) => data?.report_count ? (100 * value / data.report_count).toFixed(1) + '%' : '暂无报告'
  return <>
    <Heading title="研究分析">描述性统计与同一会话的方法比较。统计基于当前可访问的有效报告。</Heading>
    <div className="stack">
      <section className="panel toolbar"><label>统计范围<select value={scope} onChange={e => { setScope(e.target.value); setCohort(''); setPage(1) }}>
        <option value="research">研究数据</option><option value="demo">工程演示</option>
      </select></label>
      <label>分析数据源<select value={cohort} onChange={e => { setCohort(e.target.value); setPage(1) }}>
        <option value="">所有授权数据源</option>{sources.data?.items.filter(item => !!item.demo === (scope === 'demo')).map(item =>
          <option key={item.id} value={item.id}>{item.name}</option>)}
      </select></label>{scope === 'demo' && <Badge tone="warn">模拟证据，仅用于工程演示</Badge>}</section>
      <ErrorNotice text={summary.error || compare.error || sources.error} />
      {summary.loading && <Busy />}
      {data && <>
        <section className="panel report-summary"><div><span>会话 / 有效报告</span><strong>{data.sample_count} / {data.report_count}</strong></div>
          <div><span>过程可评分率</span><strong>{ratio(data.scorable_count)}</strong></div>
          <div><span>至少一维证据不足率</span><strong>{ratio(data.insufficient_count)}</strong></div></section>
        {data.report_count === 0 ? <Empty>该范围内没有已完成报告。可先完成一次数据集评价。</Empty> : <>
          <div className="grid two"><section className="panel"><h2>过程分分布（满分 5）</h2><Bars data={distribution(data.process, 5)} /></section>
            <section className="panel"><h2>系统与本地成果分分布</h2><Bars data={distribution(data.outcome)} /></section>
            <section className="panel"><h2>方法</h2><Bars data={data.methods} label={name => methodNames[name] || name} /></section>
            <section className="panel"><h2>诊断</h2><Bars data={data.diagnoses} /></section>
          </div>
          <section className="panel stack"><h2>过程维度分布</h2>
            {Object.entries(data.dimensions_by_rubric).map(([version, dimensions]) => <div key={version}>
              <h3>{fivePointRubric(version) ? '1–5 分' : '历史等级 0–3'} · {version}</h3>
              <div className="grid two">{Object.entries(dimensions).map(([key, rows]) => <div key={key}>
                <h4>{dimensionNames[key]}</h4><Bars data={rows} label={name => statusNames[name] || name + ' 分'} />
              </div>)}</div>
            </div>)}
          </section>
          {!!data.external_assignments.length && <section className="panel">
            <h2>来源作业成绩对照</h2>
            <p>{data.external_assignments.length} 个会话有关联的来源成绩。保留源文件归一化口径，均值 {
              (data.external_assignments.reduce((sum, value) => sum + value, 0) / data.external_assignments.length).toFixed(3)
            }。这些成绩独立于系统成果分。</p>
          </section>}
        </>}
      </>}
      <section className="panel stack"><h2>方法对比与分歧</h2>{compare.loading && <Busy />}
        {compare.data?.items.length === 0 && <Empty>当前没有可并列比较的方法或系统与教师分歧。</Empty>}
        {compare.data?.items.map(item => <article className="list-card" key={item.session_id}>
          <div className="row"><a href={'#/session/' + item.session_id}>打开会话</a>
            <span>{item.method_disagreement && '方法分歧 '}{item.teacher_disagreement && '教师修改'}</span></div>
          <div className="table-wrap"><table><thead><tr><th>方法</th><th>过程</th><th>成果</th><th>诊断</th></tr></thead>
            <tbody>{item.reports.map(report => <tr key={report.id}>
              <td><a href={'#/report/' + report.run_id}>{methodNames[report.actual_method]}</a>{report.degraded && ' · 降级'}</td>
              <td>{processDisplay(report.process_score, report.process_score_five)}{report.system && '（系统 ' +
                processDisplay(report.system.process_score, report.system.process_score_five) + '）'}</td>
              <td>{report.outcome_score != null && `${report.outcome_score} · ${outcomeName(report.outcome_basis)}`}</td><td>{report.diagnosis || '暂不形成'}</td>
            </tr>)}</tbody></table></div>
        </article>)}
        {compare.data && <Pager page={page} total={compare.data.total} size={20} set={setPage} />}
      </section>
      <section className="panel stack"><h2>验证样本导出</h2><p className="muted">按参与者假名分组，生成训练、开发与测试拆分和人工复核字段。</p>
        <label>研究数据源<select value={source} onChange={e => setSource(e.target.value)}>
          <option value="">选择数据源</option>{sources.data?.items.filter(item => !item.demo).map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select></label>
        {source && <ExportCard key={source} id={source} validation />}
      </section>
    </div>
  </>
}
