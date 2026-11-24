import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { ApiError, api, message } from './api'
import type { Page, Session } from './api'
import { Badge, Busy, Empty, ErrorNotice, Heading, statusNames, useData } from './ui'

export type Task = {
  id: string, title: string, version: string, instructions: string, minutes: number | null, deliverables: string[],
  status: string, has_material: boolean, public_tests: { id: string, description: string }[],
  rubric: { item_id: string, title: string, required: boolean, max_score: number }[],
  task_type: string, difficulty: string | null, ai_allowed: boolean, goal: string, materials: string[],
  attribution: string | null, static_demo: boolean, result_label: string, check_label: string,
}
type Chat = { type: string, text: string, simulated: boolean }
type Work = { id: string, status: string, task: Task, can_edit: boolean, artifact_version: number, content: string, messages: Chat[] }
type Run = {
  artifact_version: number, message: string, run: { stdout: string },
  test: { passed: number, failed: number, cases: { id: string, passed: boolean, message?: string }[] },
}

export function LiveTasks() {
  const tasks = useData<{ items: Task[] }>('/task-definitions')
  const sessions = useData<Page<Session>>('/sessions?source_type=live')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const starts = useRef(new Map<string, string>())
  async function start(task: Task) {
    setBusy(task.id); setError('')
    const eventId = starts.current.get(task.id) || crypto.randomUUID()
    starts.current.set(task.id, eventId)
    try {
      const session = await api<Session>('/live-sessions', 'POST', { task_id: task.id, client_event_id: eventId })
      starts.current.delete(task.id)
      location.hash = '#/live/' + session.id
    } catch (e) { setError(message(e)) } finally { setBusy('') }
  }
  const published = tasks.data?.items.filter(task => task.status === 'published')
  return <>
    <Heading title="真人任务">选择课堂任务，使用本地材料作答并提交成果。</Heading>
    <ErrorNotice text={error || tasks.error || sessions.error} />
    <div className="task-home">
      <section className="panel stack"><h2>学生任务库</h2>
        {tasks.loading && <Busy />}
        {published?.length === 0 && <Empty>当前没有已发布的任务。</Empty>}
        <div className="task-library">{published?.map(task => <article className="task-library-card" aria-label={task.title} key={task.id}>
          <div className="chips"><Badge>{task.task_type}</Badge>{task.difficulty && <Badge>{task.difficulty}</Badge>}
            {task.minutes && <span className="muted">{task.minutes} 分钟</span>}</div>
          <h3>{task.title}</h3><p>{task.goal}</p>
          <dl><div><dt>输入材料</dt><dd>{task.materials.length ? task.materials.join('、') : '见任务说明'}</dd></div>
            <div><dt>交付物</dt><dd>{task.deliverables.join('、') || '见任务说明'}</dd></div>
            <div><dt>AI</dt><dd>{task.ai_allowed ? '允许 · 工作台提供离线辅助' : '本任务不允许使用'}</dd></div></dl>
          {task.attribution && <small className="muted">{task.attribution}</small>}
          <button disabled={!!busy} onClick={() => start(task)}>{busy === task.id ? '正在开始…' : '开始任务'}</button>
        </article>)}</div>
      </section>
      <section className="panel stack"><h2>最近作答</h2>
        {sessions.loading && <Busy />}
        {sessions.data?.items.length === 0 && <Empty>还没有作答记录。</Empty>}
        {sessions.data?.items.map(session => <a key={session.id} className="list-card" href={
          session.status === 'in_progress' ? '#/live/' + session.id : '#/session/' + session.id}>
          <strong>{session.task_title}</strong><span className="muted">{statusNames[session.status]}</span>
        </a>)}
      </section>
    </div>
  </>
}

export function Workbench({ id }: { id: string }) {
  const [refresh, setRefresh] = useState(0)
  const work = useData<Work>('/live-sessions/' + id, refresh)
  return work.data ? <Editor key={id + '/' + refresh} work={work.data} reload={() => setRefresh(old => old + 1)} /> : <>
    <Heading title="作答工作台" /><ErrorNotice text={work.error} />{work.loading && <Busy />}
  </>
}

async function sha256(text: string) {
  const checksum = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text))
  return [...new Uint8Array(checksum)].map(byte => byte.toString(16).padStart(2, '0')).join('')
}

function actionId(ref: Map<string, { stamp: string, id: string }>, kind: string, stamp: string) {
  const prior = ref.get(kind)
  if (prior?.stamp === stamp) return prior.id
  const id = crypto.randomUUID()
  ref.set(kind, { stamp, id })
  return id
}

function Editor({ work, reload }: { work: Work, reload: () => void }) {
  const [content, setContent] = useState(work.content)
  const [version, setVersion] = useState(work.artifact_version)
  const [savedContent, setSavedContent] = useState(work.content)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [conflict, setConflict] = useState(false)
  const [chat, setChat] = useState('')
  const [transcript, setTranscript] = useState(work.messages)
  const [run, setRun] = useState<Run>()
  const [statement, setStatement] = useState('')
  const [cleanedData, setCleanedData] = useState('')
  const [trend, setTrend] = useState('')
  const [confirm, setConfirm] = useState(false)
  const events = useRef(new Map<string, { stamp: string, id: string }>())
  const inFlight = useRef(false)
  const url = '/live-sessions/' + work.id

  function onError(e: unknown) {
    setError(message(e))
    if (e instanceof ApiError && e.code === 'ARTIFACT_VERSION') setConflict(true)
  }

  async function save(value: string) {
    if (inFlight.current || conflict) return
    inFlight.current = true
    setBusy('save'); setError('')
    try {
      const key = JSON.stringify([version, value])
      const eventId = actionId(events.current, 'save', key)
      const result = await api<{ version: number }>(url + '/events', 'POST', {
        type: 'code_snapshot', client_event_id: eventId, content: value, expected_artifact_version: version,
      })
      events.current.delete('save')
      setVersion(result.version); setSavedContent(value)
    } catch (e) { onError(e) } finally { inFlight.current = false; setBusy('') }
  }

  useEffect(() => {
    if (busy || conflict || !work.can_edit || content === savedContent || error) return
    const timer = setTimeout(() => { void save(content) }, 950)
    return () => clearTimeout(timer)
  }, [content, savedContent, busy, conflict, error, version, work.can_edit])

  async function send(event: FormEvent) {
    event.preventDefault()
    if (!chat.trim() || inFlight.current) return
    inFlight.current = true
    setBusy('chat'); setError('')
    const text = chat.trim()
    try {
      const reply = await api<{ text: string, simulated: boolean }>(url + '/chat', 'POST', {
        text, client_event_id: actionId(events.current, 'chat', text),
      })
      events.current.delete('chat')
      setTranscript(old => [...old, { type: 'chat_user_message', text, simulated: false }, { type: 'chat_ai_message', ...reply }])
      setChat('')
    } catch (e) { onError(e) } finally { inFlight.current = false; setBusy('') }
  }

  async function saveFull(kind: 'run' | 'submit') {
    if (inFlight.current || conflict) return
    inFlight.current = true
    setBusy(kind); setError('')
    const value = content
    try {
      const artifact_checksum = await sha256(value)
      const extras = kind === 'submit' ? { statement, cleaned_data: cleanedData, trend } : {}
      const body = { content: value, expected_artifact_version: version, artifact_checksum, ...extras }
      const eventId = actionId(events.current, kind, JSON.stringify(body))
      if (kind === 'run') {
        const result = await api<Run>(url + '/runs', 'POST', { ...body, client_event_id: eventId })
        setVersion(result.artifact_version); setSavedContent(value); setRun(result)
      } else {
        await api(url + '/submit', 'POST', { ...body, client_event_id: eventId })
        location.hash = '#/session/' + work.id
      }
      events.current.delete(kind)
    } catch (e) { onError(e) } finally { inFlight.current = false; setBusy('') }
  }
  const task = work.task
  if (!work.can_edit) return <>
    <Heading title={task.title} />
    <section className="panel stack"><p>{work.status === 'submitted' ? '该提交已冻结。' : '此会话可查看。'}</p>
      <a href={'#/session/' + work.id}>查看会话和报告</a></section>
  </>
  return <>
    <Heading title={task.title} actions={<a href="#/live" className="button secondary">返回任务列表</a>}>作答工作台 · 任务版本 {task.version}</Heading>
    <div className="stack">
      <ErrorNotice text={error} />
      {conflict && <div className="notice warn"><p>请先复制未保存的草稿，再加载服务器中的最新版本。</p>
        <button className="secondary" onClick={reload}>加载最新版本</button></div>}
      <details open className="panel"><summary>任务说明与材料</summary><p>{task.instructions}</p>
        {task.attribution && <p className="muted">{task.attribution}</p>}
        {task.has_material && <ul className="plain">{task.materials.map(file => <li key={file}>
          <a href={'/api/task-definitions/' + task.id + '/material?file=' + encodeURIComponent(file)} download>{file}</a>
        </li>)}</ul>}
        {!!task.public_tests.length && <ul>{task.public_tests.map(item => <li key={item.id}>{item.description}</li>)}</ul>}
      </details>
      <div className="grid two align-start">
        <section className="panel stack"><h2>代码编辑</h2>
          <label>Python 脚本<textarea className="code-editor" value={content} onChange={e => { setContent(e.target.value); if (!conflict) setError('') }}
            spellCheck={false} rows={13} /></label>
          <span className="muted" role="status">{busy === 'save' ? '自动保存中…' : content === savedContent ? '已保存 · 版本 ' + version : '有未保存编辑'}</span>
          <div className="toolbar">
            <button className="secondary" disabled={!!busy || conflict || content === savedContent} onClick={() => void save(content)}>保存代码快照</button>
            {task.static_demo && <button disabled={!!busy || conflict} onClick={() => void saveFull('run')}>运行静态演示</button>}
          </div>
          <p className="muted">{task.static_demo ? '运行会冻结当前快照并记录内置案例的模拟事件。' : '请在本地 Python 环境运行，并把实际输出与检查结果粘贴到提交区。'}</p>
          {run && <div className="notice warn" role="status"><b>{run.message}</b>
            <p>{run.run.stdout}</p><p>模拟测试：通过 {run.test.passed} 项，失败 {run.test.failed} 项。</p>
            <ul>{run.test.cases.map(test => <li key={test.id}>{task.public_tests.find(item => item.id === test.id)?.description ?? test.id}
              {' · ' + (test.passed ? '模拟通过' : test.message)}</li>)}</ul>
          </div>}
        </section>
        {task.ai_allowed && <section className="panel stack"><h2>AI 对话</h2>
          <p className="muted">演示提供器返回模拟回复。AI 建议可能出错，请根据题目核对。</p>
          <div className="chat-log" aria-live="polite">
            {transcript.length === 0 && <Empty>可以从澄清一个具体任务约束开始。</Empty>}
            {transcript.map((entry, i) => <div className={'chat-entry ' + (entry.simulated ? 'chat-ai' : '')} key={i}>
              <Badge>{entry.type === 'chat_user_message' ? '你' : entry.simulated ? 'AI · 模拟回复' : 'AI'}</Badge><p>{entry.text}</p>
            </div>)}
          </div>
          <form className="stack" onSubmit={send}>
            <label>发送给 AI<textarea value={chat} onChange={e => setChat(e.target.value)} maxLength={16000} rows={3} /></label>
            <button disabled={!!busy || !chat.trim()}>发送消息</button>
          </form>
        </section>}
      </div>
      <section className="panel stack"><h2>提交成果</h2>
        <div className="chips">{task.deliverables.map(item => <Badge key={item}>{item}</Badge>)}</div>
        <div className="grid two">
          <label>{task.result_label}<textarea rows={4} value={cleanedData} onChange={e => setCleanedData(e.target.value)} /></label>
          <label>{task.check_label}<textarea rows={4} value={trend} onChange={e => setTrend(e.target.value)} /></label>
        </div>
        <label>做法和局限说明<textarea rows={3} value={statement} onChange={e => setStatement(e.target.value)} /></label>
        <label className="check"><input type="checkbox" checked={confirm} onChange={e => setConfirm(e.target.checked)} />确认冻结当前版本并排队评价</label>
        <button disabled={!!busy || conflict || !confirm} onClick={() => void saveFull('submit')}>提交并生成评价</button>
      </section>
    </div>
  </>
}
