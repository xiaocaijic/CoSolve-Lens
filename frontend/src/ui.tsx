import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { api, message, terminal } from './api'
import type { Job } from './api'

export const statusNames: Record<string, string> = {
  queued: '排队中', running: '处理中', completed: '已完成', partial: '部分完成', failed: '失败',
  cancel_requested: '等待取消', cancelled: '已取消', submitted: '已提交', in_progress: '进行中',
  scored: '已评分', not_applicable: '不适用', insufficient_evidence: '证据不足',
  missing_evidence: '缺少成果证据', reviewed: '已复核', system: '系统报告', new: '未开始',
}
export const methodNames: Record<string, string> = {
  rules: '规则法', direct_llm: '直接 LLM 法', hybrid: '混合法', demo_conflict: 'F5C 冲突演示',
}
export const fiveDimensionNames: Record<string, string> = {
  problem_understanding: '问题理解', information_discernment: '信息甄别', analytical_reasoning: '分析推理',
  result_evaluation: '结果评价', strategy_adjustment: '策略调整',
}
export const legacyDimensionNames: Record<string, string> = {
  organization: '任务理解与组织', verification: '检查与验证', diagnosis: '诊断与调整', ai_integration: 'AI 使用与整合',
}
export const dimensionNames = { ...fiveDimensionNames, ...legacyDimensionNames }
export const coverageNames: Record<string, string> = {
  process_and_outcome: '过程与成果', partial_process: '部分过程', outcome_only: '仅成果', none: '无法评价',
}
export const eventNames: Record<string, string> = {
  task_opened: '打开任务', chat_user_message: '学生消息', chat_ai_message: 'AI 回复',
  code_snapshot: '代码快照', run_started: '请求运行', run_finished: '运行结束', test_finished: '测试结果',
  artifact_saved: '保存交付物', submission_created: '提交成果', task_closed: '关闭任务',
  editor_focus: '编辑焦点', custom: '其他来源事件',
}

export function useData<T>(path: string | null, refresh = 0) {
  const [state, setState] = useState<{ data?: T, error?: string, loading: boolean }>({ loading: !!path })
  useEffect(() => {
    let active = true
    if (!path) { setState({ loading: false }); return }
    setState({ loading: true })
    api<T>(path).then(data => active && setState({ data, loading: false }),
      error => active && setState({ error: message(error), loading: false }))
    return () => { active = false }
  }, [path, refresh])
  return state
}

export function useJob(jobId: string | null) {
  const [job, setJob] = useState<Job>()
  const [error, setError] = useState('')
  useEffect(() => {
    setJob(undefined)
    setError('')
    if (!jobId) return
    let active = true
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      try {
        const current = await api<Job>('/jobs/' + jobId)
        if (!active) return
        setJob(current); setError('')
        if (!terminal(current.status)) timer = setTimeout(poll, 650)
      } catch (e) {
        if (active) { setError(message(e)); timer = setTimeout(poll, 2000) }
      }
    }
    void poll()
    return () => { active = false; clearTimeout(timer) }
  }, [jobId])
  return { job, error }
}

export function Heading({ title, children, actions }: { title: string, children?: ReactNode, actions?: ReactNode }) {
  return <header className="heading"><div><h1>{title}</h1>{children && <p>{children}</p>}</div>{actions}</header>
}
export function ErrorNotice({ text }: { text?: string }) {
  return text ? <div className="notice error" role="alert">{text}</div> : null
}
export function Badge({ children, tone = 'neutral' }: { children: ReactNode, tone?: string }) {
  return <span className={'badge ' + tone}>{children}</span>
}
export function Busy() { return <p role="status" className="muted">正在加载…</p> }
export function Empty({ children }: { children: ReactNode }) { return <div className="empty">{children}</div> }
export function displayScore(value: number | null, fallback: string) { return value === null ? fallback : String(value) }
export function processDisplay(percent: number | null | undefined, direct?: number | null, fallback = '证据不足') {
  return percent == null ? fallback : `${direct ?? Math.round(percent * 5 + 1e-8) / 100} / 5`
}
export function fivePointRubric(version: string) { return version === 'bars-3.0-five-point' }
export function outcomeName(basis: string | null | undefined) {
  return basis === 'source_grade' ? '来源成果分' : basis === 'local_review' ? '本地成果分' : '系统成果分'
}
export function time(value: string | null) {
  return value ? new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : '仅记录顺序'
}
export function Pager({ page, total, size, set }: { page: number, total: number, size: number, set: (p: number) => void }) {
  return total > size && <div className="pager">
    <button type="button" className="secondary" disabled={page <= 1} onClick={() => set(page - 1)}>上一页</button>
    <span>{page} / {Math.ceil(total / size)}</span>
    <button type="button" className="secondary" disabled={page * size >= total} onClick={() => set(page + 1)}>下一页</button>
  </div>
}
export function JobCard({ job }: { job?: Job }) {
  if (!job) return null
  return <div className="notice" role="status">
    <b>{statusNames[job.status] || job.status}</b><span> · {job.progress_done} / {job.progress_total}</span>
    <progress value={job.progress_done} max={job.progress_total || 1} />
    <span>成功 {job.succeeded_count} · 失败 {job.failed_count} · 跳过 {job.skipped_count}</span>
    {job.error && <p>{job.error.message}</p>}
    {job.runs && <ul className="plain">{job.runs.map((run, index) => <li key={run.id}>
      {['completed', 'partial'].includes(run.status) ? <a href={'#/report/' + run.id}>查看报告 {index + 1}</a> : '记录 ' + (index + 1) + ' · ' + (statusNames[run.status] || run.status)}
      {run.provider === 'deepseek' && <span> · DeepSeek 已返回</span>}
      {run.degraded === 1 && run.provider === 'rules' && <span> · 已降级为规则，未获得模型结果</span>}
      {run.error && <span> · {JSON.parse(run.error).message}</span>}
    </li>)}</ul>}
  </div>
}
