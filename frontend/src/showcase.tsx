import { useState } from 'react'
import { Badge, Busy, Empty, ErrorNotice, Heading, processDisplay, useData } from './ui'

export type EntryMode = 'replay' | 'live'
export type EntrySettings = { replay_enabled: boolean, live_enabled: boolean, default_mode: EntryMode }

export type ShowcaseSummary = {
  id: string, title: string, theme: string, task: string, hook: string, session_id: string | null,
  report: SavedReport | null, historical_report: SavedReport | null,
}

type SavedReport = { id: string, run_id: string, rubric_version: string, process_score: number | null,
  process_score_five: number | null, source_outcome_percent: number | null }

type CaseDetail = ShowcaseSummary & {
  finding: string, exercise: string,
  evidence: { id: string, sequence: number, role: string, caption: string, excerpt: string, excerpted: boolean }[],
}

export function ShowcaseLibrary({ cases }: { cases: ShowcaseSummary[] }) {
  const [filter, setFilter] = useState('')
  const themes = [...new Set(cases.map(item => item.theme))]
  const shown = cases.filter(item => !filter || (filter === 'report' ? item.report : item.theme === filter))
  const reportCount = cases.filter(item => item.report).length
  return <section className="stack" aria-labelledby="showcase-library">
    <div className="panel stack showcase-lead">
      <div><h2 id="showcase-library">StudyChat 精选案例</h2>
        <p>{cases.length} 个课堂案例 · {reportCount} 份正式存档报告 · {cases.length - reportCount} 份证据讲评</p></div>
      <p className="muted">打开案例先看原对话证据，再展开预备讲评。课堂回放使用已有材料，模型调用 0 次。</p>
      <div className="toolbar">
        <label>按主题查看<select value={filter} onChange={event => setFilter(event.target.value)}>
          <option value="">全部案例</option><option value="report">有正式存档报告</option>
          {themes.map(theme => <option key={theme}>{theme}</option>)}
        </select></label>
      </div>
    </div>
    {!shown.length && <Empty>{cases.length ? '当前分类没有案例。' : '没有可访问的精选案例。可在现场分析中导入内置 StudyChat。'}</Empty>}
    <div className="showcase-grid">
      {shown.map(item => <article key={item.id} className="panel stack showcase-card">
        <div className="chips"><Badge>{item.task}</Badge><Badge>{item.theme}</Badge></div>
        <h3><a href={`#/showcase/${item.id}`}>{item.title}</a></h3>
        <p>{item.hook}</p>
        <small className="muted">{item.report ? '已有五分制正式报告' : '课堂证据讲评'}</small>
        <a className="button secondary" href={`#/showcase/${item.id}`}>打开案例</a>
      </article>)}
    </div>
  </section>
}

export function ShowcaseCase({ id }: { id: string }) {
  const data = useData<CaseDetail>(`/studychat/showcase/${encodeURIComponent(id)}`)
  const [revealed, setRevealed] = useState(false)
  if (data.error) return <div className="stack"><ErrorNotice text={data.error} /><a href="#/datasets">返回数据集评价</a></div>
  const item = data.data
  if (!item) return <Busy />
  const report = item.report
  const nativeFive = report?.rubric_version === 'bars-3.0-five-point'
  return <div className="stack showcase-case">
    <Heading title={item.title} actions={<a className="button secondary" href="#/datasets/replay">返回精选案例</a>}>
      {item.task} · {item.theme} · 课堂回放
    </Heading>
    <section className="panel stack"><h2>观察问题</h2><p>{item.hook}</p>
      <div className="chips"><Badge>预备内容 · 0 次模型调用</Badge>
        <Badge>{report ? '正式报告已存档' : '课堂证据讲评'}</Badge></div>
    </section>
    <section className="panel stack"><h2>关键对话证据</h2>
      {!item.evidence.length && <Empty>当前实例尚未关联此案例的 StudyChat 快照。</Empty>}
      {item.evidence.map(ref => <details key={ref.id} className="showcase-evidence"
        open={!ref.excerpted && ref.excerpt.length <= 300 && ref.excerpt.split('\n').length <= 9}>
        <summary><b>E{String(ref.sequence).padStart(4, '0')} · {ref.role}</b><span>{ref.caption}</span></summary>
        <blockquote>{ref.excerpt || '（空消息）'}</blockquote>
        {ref.excerpted && <small className="muted">此处显示节选，完整内容见原始会话。</small>}
      </details>)}
      {item.session_id && <a href={`#/session/${item.session_id}`}>查看完整原始会话</a>}
    </section>
    <section className="panel stack">
      <div className="row"><h2>预备讲评</h2>
        <button type="button" aria-expanded={revealed} aria-controls="showcase-answer" onClick={() => setRevealed(open => !open)}>
          {revealed ? '收起预备讲评' : '展开预备讲评'}</button></div>
      {revealed && <div id="showcase-answer" className="stack">
        <div><h3>证据支持的结论</h3><p>{item.finding}</p></div>
        <div><h3>课堂练习</h3><p>{item.exercise}</p></div>
        {report && <div className="notice stack">
          <p>已存档的五维报告{report.process_score != null && ` · 过程分 ${processDisplay(report.process_score, report.process_score_five)}${nativeFive ? '' : '（历史折算）'}`}
            {report.source_outcome_percent != null && ` · 来源成果分 ${report.source_outcome_percent}`}</p>
          <a href={`#/session/${item.session_id}/${report.run_id}/${report.id}`}>打开当前默认正式报告</a>
          {item.historical_report && <a href={`#/session/${item.session_id}/${item.historical_report.run_id}/${item.historical_report.id}`}>
            查看历史三分制报告</a>}
        </div>}
      </div>}
    </section>
  </div>
}
