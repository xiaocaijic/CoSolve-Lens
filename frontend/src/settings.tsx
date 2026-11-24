import { useState } from 'react'
import type { FormEvent } from 'react'
import { api, message } from './api'
import type { Task } from './live'
import type { EntryMode, EntrySettings } from './showcase'
import { Badge, Busy, Empty, ErrorNotice, Heading, dimensionNames, time, useData } from './ui'

type Provider = {
  provider: string, enabled: boolean, model: string, url: string, timeout: number, retention_confirmed: boolean,
  usage_note: string, key_configured: boolean, deepseek_key_configured: boolean,
}
type Thresholds = {
  id: string, process_threshold: number, outcome_threshold: number, rubric_version: string, threshold_version: string,
  prompt_version: string, ruleset_version: string, bars: Record<string, string[]>,
}
type Version = Omit<Thresholds, 'process_threshold' | 'outcome_threshold' | 'bars'> & { created_at: string }
const deepseekModels = [
  { id: 'deepseek-flash', name: 'DeepSeek V4.1 Flash' },
  { id: 'deepseek-v4-pro', name: 'DeepSeek V4 Pro' },
]

export function Settings() {
  const [refresh, setRefresh] = useState(0)
  const provider = useData<Provider>('/settings/llm', refresh)
  const evaluation = useData<Thresholds>('/settings/evaluation', refresh)
  const versions = useData<{ items: Version[] }>('/settings/versions', refresh)
  const entries = useData<EntrySettings>('/studychat/entry-settings', refresh)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [confirm, setConfirm] = useState('')
  const [providerChoice, setProviderChoice] = useState<string>()
  const [deepseekChoice, setDeepseekChoice] = useState<string>()
  const selectedProvider = providerChoice ?? (provider.data?.model ? provider.data.provider : 'deepseek')
  const selectedDeepSeekModel = deepseekChoice ??
    deepseekModels.find(item => provider.data?.provider === 'deepseek' && item.id === provider.data.model)?.id ??
    deepseekModels[0].id
  async function change(path: string, method: string, body?: unknown, success = '已保存。') {
    setError(''); setNotice(''); setBusy(true)
    try {
      const result = await api<{ message?: string, deleted_data_sources?: number }>(path, method, body)
      setNotice(result.message || (result.deleted_data_sources === undefined ? success : '已删除 ' + result.deleted_data_sources + ' 个演示数据源。'))
      setRefresh(old => old + 1)
    } catch (e) { setError(message(e)) } finally { setBusy(false) }
  }
  function updateProvider(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    void change('/settings/llm', 'PUT', { provider: selectedProvider, model: form.get('model'), url: form.get('url') || '',
      timeout: Number(form.get('timeout')), enabled: form.has('enabled'), retention_confirmed: form.has('retention_confirmed'),
      usage_note: form.get('usage_note'),
    })
  }
  function saveKey(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    const key = new FormData(form).get('deepseek_key')
    form.reset()
    void change('/settings/deepseek-key', 'PUT', { key }, 'DeepSeek 密钥已保存到本机凭据。')
  }
  async function testDeepSeek() {
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await api<{ message: string, model: string }>(
        '/settings/deepseek-test', 'POST', { model: selectedDeepSeekModel },
      )
      setNotice(result.model + ' · ' + result.message)
    } catch (e) { setError(message(e)) } finally { setBusy(false) }
  }
  function updateThresholds(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    void change('/settings/evaluation', 'PUT', { process_threshold: Number(form.get('process')) * 20, outcome_threshold: Number(form.get('outcome')) })
  }
  return <>
    <Heading title="系统设置" actions={<a className="button secondary" href="#/tasks">任务管理</a>}>管理提供器、版本化阈值和演示环境。</Heading>
    <div className="stack">
      <ErrorNotice text={error || provider.error || evaluation.error || versions.error || entries.error} />
      {notice && <div className="notice" role="status">{notice}</div>}
      {entries.data && <StudyChatEntrySettings key={refresh} initial={entries.data} busy={busy}
        save={body => void change('/settings/studychat-entries', 'PUT', body, 'StudyChat 入口设置已保存。')} />}
      <div className="grid two align-start">
        <section className="panel stack"><h2>评价模型</h2>
          {provider.loading && <Busy />}
          {provider.data && <>
            <label>模型供应商<select value={selectedProvider} onChange={e => setProviderChoice(e.target.value)}>
              <option value="deepseek">DeepSeek</option><option value="openai-compatible">其他 OpenAI 兼容接口</option>
            </select></label>
            {selectedProvider === 'deepseek' && <>
              <form className="stack" onSubmit={saveKey}>
                <label>DeepSeek API Key<input name="deepseek_key" type="password" autoComplete="off" minLength={12} maxLength={512} required /></label>
                <div className="toolbar"><button disabled={busy}>保存 DeepSeek Key</button>
                  <Badge>{provider.data.deepseek_key_configured ? '已配置' : '未配置'}</Badge>
                </div>
              </form>
              <button className="secondary" disabled={busy || !provider.data.deepseek_key_configured} onClick={() => void testDeepSeek()}>
                连接测试（仅合成数据）
              </button>
            </>}
            <form className="stack" key={refresh + selectedProvider} onSubmit={updateProvider}>
            {selectedProvider === 'deepseek' ? <label>模型名称<select name="model" value={selectedDeepSeekModel}
              onChange={event => setDeepseekChoice(event.target.value)}>
              {deepseekModels.map(item => <option key={item.id} value={item.id}>{item.name} · {item.id}</option>)}
            </select></label> : <>
              <label>模型名称<input name="model" defaultValue={provider.data.model} maxLength={100} /></label>
              <label>HTTPS 模型地址<input name="url" type="url" defaultValue={provider.data.url} maxLength={300} /></label>
            </>}
            <label>超时（秒）<input name="timeout" type="number" min={1} max={90}
              defaultValue={provider.data.provider === selectedProvider ? provider.data.timeout : 45} required /></label>
            <label>外发用途与保留策略<textarea name="usage_note" maxLength={1000} defaultValue={provider.data.usage_note} /></label>
            <label className="check"><input name="retention_confirmed" type="checkbox" defaultChecked={provider.data.retention_confirmed} />确认供应商数据用途与保留策略</label>
            <label className="check"><input name="enabled" type="checkbox" defaultChecked={provider.data.enabled} />允许对授权来源启用外部评价</label>
            <button disabled={busy}>保存模型配置</button>
          </form></>}
          <button className="secondary" disabled={busy} onClick={() => void change('/settings/provider-check', 'POST')}>运行配置自检</button>
        </section>
        <section className="panel stack"><h2>量规与诊断阈值</h2>
          {evaluation.loading && <Busy />}
          {evaluation.data && <form className="stack" key={evaluation.data.id} onSubmit={updateThresholds}>
            <label>过程达标阈值（满分 5）<input name="process" type="number" required min={0} max={5} step="0.005" defaultValue={evaluation.data.process_threshold / 20} /></label>
            <label>成果达标阈值<input name="outcome" type="number" required min={0} max={100} step="0.1" defaultValue={evaluation.data.outcome_threshold} /></label>
            <button disabled={busy}>保存阈值版本</button>
            <details><summary>五维行为锚点 · {evaluation.data.rubric_version}</summary>
              {Object.keys(evaluation.data.bars).map(name => <div className="divider" key={name}>
                <h3>{dimensionNames[name]}</h3><ul className="plain">{Object.entries(evaluation.data!.bars[name])
                  .sort((a, b) => Number(b[0]) - Number(a[0])).map(([score, text]) => <li key={score}><b>{score} 分</b> · {text}</li>)}</ul>
              </div>)}
            </details>
          </form>}
        </section>
      </div>
      <section className="panel"><h2>配置版本</h2>
        {versions.data?.items.length === 0 && <Empty>当前没有已写入的配置版本。</Empty>}
        <ul className="plain">{versions.data?.items.map(row => <li key={row.id}>
          <b>{row.threshold_version}</b> · {row.rubric_version} · {row.prompt_version} · {row.ruleset_version} · {time(row.created_at)}
        </li>)}</ul>
      </section>
      <details className="panel"><summary>重置 Demo 数据源</summary>
        <form className="stack divider" onSubmit={e => {
          e.preventDefault(); void change('/settings/reset-demo', 'POST', { confirmation: confirm }); setConfirm('')
        }}>
          <p>重置将删除明确标记为演示的数据源、会话、报告及导出。</p>
          <label>输入 RESET DEMO 确认<input value={confirm} onChange={e => setConfirm(e.target.value)} /></label>
          <button disabled={busy || confirm !== 'RESET DEMO'}>重置演示数据</button>
        </form>
      </details>
    </div>
  </>
}

function StudyChatEntrySettings({ initial, busy, save }: {
  initial: EntrySettings, busy: boolean, save: (value: EntrySettings) => void,
}) {
  const [config, setConfig] = useState(initial)
  function toggle(mode: EntryMode, enabled: boolean) {
    setConfig(old => ({ ...old, [`${mode}_enabled`]: enabled,
      default_mode: !enabled && old.default_mode === mode ? mode === 'replay' ? 'live' : 'replay' : old.default_mode }))
  }
  return <section className="panel stack"><h2>StudyChat 入口设置</h2>
    <form className="stack" onSubmit={event => { event.preventDefault(); save(config) }}>
      <div className="toolbar">
        <label className="check"><input type="checkbox" checked={config.replay_enabled}
          onChange={event => toggle('replay', event.target.checked)} />开放课堂回放</label>
        <label className="check"><input type="checkbox" checked={config.live_enabled}
          onChange={event => toggle('live', event.target.checked)} />开放现场分析</label>
        <label>默认入口<select value={config.default_mode} onChange={event => setConfig({ ...config, default_mode: event.target.value as EntryMode })}>
          <option value="replay" disabled={!config.replay_enabled}>课堂回放</option>
          <option value="live" disabled={!config.live_enabled}>现场分析</option>
        </select></label>
      </div>
      <p className="muted">回放直接使用预备案例。现场分析创建新任务，并沿用逐条模型授权。</p>
      <button disabled={busy || !config.replay_enabled && !config.live_enabled}>保存入口设置</button>
    </form>
  </section>
}

const defaultItems: Task['rubric'] = [
  { item_id: 'function', title: '核心功能和测试', max_score: 50, required: true },
  { item_id: 'constraints', title: '任务限制满足情况', max_score: 20, required: true },
  { item_id: 'quality', title: '交付质量', max_score: 15, required: true },
  { item_id: 'explanation', title: '做法和局限说明', max_score: 15, required: true },
]

export function TaskManagement({ admin }: { admin: boolean }) {
  const [revision, setRevision] = useState(0)
  const [active, setActive] = useState<Task>()
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const data = useData<{ items: Task[] }>('/task-definitions', revision)
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError(''); setNotice('')
    try {
      const form = new FormData(event.currentTarget)
      const deliverables = String(form.get('deliverables')).split('\n').map(line => line.trim()).filter(Boolean)
      let rubric, public_tests
      try {
        rubric = JSON.parse(String(form.get('rubric')))
        public_tests = JSON.parse(String(form.get('tests')))
      } catch { throw new Error('成果量规和公开测试需要有效 JSON。') }
      const next = await api<Task>('/task-definitions' + (active ? '/' + active.id : ''), active ? 'PUT' : 'POST', {
        title: form.get('title'), instructions: form.get('instructions'), minutes: Number(form.get('minutes')), deliverables, rubric, public_tests,
      })
      setActive(next); setNotice('草稿已保存。'); setRevision(old => old + 1)
    } catch (e) { setError(message(e)) } finally { setBusy(false) }
  }
  async function publish() {
    if (!active) return
    setBusy(true); setError(''); setNotice('')
    try {
      await api('/task-definitions/' + active.id + '/publish', 'POST')
      setActive(undefined); setNotice('任务版本已发布。'); setRevision(old => old + 1)
    } catch (e) { setError(message(e)) } finally { setBusy(false) }
  }
  return <>
    <Heading title="任务管理" actions={<a href="#/live" className="button secondary">查看已发布任务</a>}>编辑草稿、配置成果量规和公开测试。</Heading>
    <ErrorNotice text={error || data.error} />{notice && <div className="notice">{notice}</div>}
    <div className="grid two align-start">
      <section className="panel stack"><h2>任务版本</h2>
        {data.loading && <Busy />}
        {data.data?.items.map(item => <article className="list-card" key={item.id}><div className="row">
          <strong>{item.title}</strong><Badge>{item.status === 'draft' ? '草稿' : '已发布'}</Badge>
        </div><span className="muted">版本 {item.version}</span>
          {item.status === 'draft' && <button className="secondary" onClick={() => setActive(item)}>编辑此草稿</button>}</article>)}
        {active && <button className="secondary" onClick={() => setActive(undefined)}>新建草稿</button>}
      </section>
      <section className="panel stack"><h2>{active ? '编辑草稿' : '新建草稿'}</h2>
        <form className="stack" onSubmit={save} key={(active?.id || '') + '/' + revision}>
          <label>名称<input name="title" required maxLength={160} defaultValue={active?.title} /></label>
          <label>任务说明<textarea name="instructions" required maxLength={12000} rows={4} defaultValue={active?.instructions} /></label>
          <label>预计时长（分钟）<input name="minutes" type="number" required min={1} max={400} defaultValue={active?.minutes || 45} /></label>
          <label>预期交付物（每行一项）<textarea name="deliverables" required defaultValue={active?.deliverables.join('\n')} /></label>
          <label>成果量规 JSON<textarea name="rubric" className="code-editor" required rows={8} defaultValue={JSON.stringify(active?.rubric ?? defaultItems, null, 2)} /></label>
          <label>公开测试 JSON<textarea name="tests" className="code-editor" required rows={3} defaultValue={JSON.stringify(active?.public_tests ?? [], null, 2)} /></label>
          <div className="toolbar"><button disabled={busy}>保存草稿</button>
            {admin && active && <button type="button" disabled={busy} className="secondary" onClick={() => void publish()}>发布已保存草稿</button>}</div>
        </form>
      </section>
    </div>
  </>
}
