import { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { ApiError, api, message, setUser } from './api'
import type { User } from './api'
import { Dataset, Datasets } from './datasets'
import { ShowcaseCase } from './showcase'
import { ReportView, SessionView } from './reports'
import { LiveTasks, Workbench } from './live'
import { ReviewForm, ReviewQueue } from './review'
import { Analytics } from './analysis'
import { Settings, TaskManagement } from './settings'
import { Badge, Busy, Empty, ErrorNotice, Heading, useData } from './ui'
import './style.css'

const roles = { student: '学生', teacher: '教师', admin: '管理员' }
const demoAccounts = { student: 'student-demo', teacher: 'teacher-demo', admin: 'admin-demo' }
const loginDestinations = { student: '#/live', teacher: '#/datasets', admin: '#/settings' }
type Role = User['role']

function roleEntry(route: string, id: string): Role | undefined {
  return route === 'login' && Object.hasOwn(roles, id) ? id as Role : undefined
}

function useRoute() {
  const get = () => window.location.hash.replace(/^#\/?/, '').split('/').filter(Boolean)
  const [route, setRoute] = useState(get)
  useEffect(() => {
    const onHash = () => { setRoute(get()); window.scrollTo(0, 0) }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])
  return route
}

function Login({ accept, role }: { accept: (user: User) => void, role?: Role }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function login(selected: Role) {
    setBusy(true); setError('')
    try {
      const user = await api<User>('/auth/demo', 'POST', { account: demoAccounts[selected] })
      location.hash = loginDestinations[user.role]
      accept(user)
    } catch (e) { setError(message(e)) } finally { setBusy(false) }
  }
  return <main className="login">
    <section className="login-label"><span className="wordmark">CoSolve Lens</span>
      <h1>AI 协同问题<br />解决评价系统</h1>
      <p>查看大学生使用 AI 解决计算机科学任务时的过程证据和成果反馈。</p>
    </section>
    <section className="login-card panel"><h2>{role ? roles[role] + '登录' : '演示登录'}</h2>
      <p className="muted">{role ? '使用演示' + roles[role] + '账号进入系统。' : '选择身份进入本地演示。'}</p>
      <div className="stack">
        {(role ? [role] : ['teacher', 'student', 'admin'] as const).map((item, index) =>
          <button key={item} disabled={busy} className={index ? 'secondary' : undefined} onClick={() => login(item)}>
            以{roles[item]}身份登录
          </button>)}
      </div><ErrorNotice text={error} />
    </section>
  </main>
}

function App() {
  const [user, update] = useState<User | null>()
  const [error, setError] = useState('')
  const [route, id, version, reportVersion] = useRoute()
  const loginRole = roleEntry(route, id)
  const [entry, setEntry] = useState<Role | undefined>(() => loginRole)
  useEffect(() => { if (loginRole) setEntry(loginRole) }, [loginRole])
  useEffect(() => {
    document.title = entry ? roles[entry] + ' · CoSolve Lens' : 'CoSolve Lens · AI 协同问题解决评价系统'
  }, [entry])
  const accept = (current: User | null) => { setUser(current); update(current) }
  useEffect(() => { api<User>('/auth/me').then(accept, e => {
    if (!(e instanceof ApiError && e.status === 401)) setError(message(e))
    accept(null)
  }) }, [])
  async function logout() {
    try { await api('/auth/logout', 'POST'); accept(null); location.hash = entry ? '#/login/' + entry : '#/' }
    catch (e) { setError(message(e)) }
  }
  if (user === undefined) return <main className="center"><Busy /></main>
  if (!user || loginRole) return <><ErrorNotice text={error} /><Login accept={accept} role={loginRole ?? entry} /></>
  const isTeacher = user.role !== 'student'
  const admin = user.role === 'admin'
  const links = [['', '首页'], ...(isTeacher ? [['datasets', '数据集评价']] : []), ['live', '真人任务'],
    ...(isTeacher ? [['reviews', '教师复核'], ['analytics', '研究分析']] : []), ...(admin ? [['settings', '系统设置']] : [])]
  let screen
  if (route === 'datasets' && isTeacher) screen = id && id !== 'replay' && id !== 'live'
    ? <Dataset key={id} id={id} /> : <Datasets key={id ?? 'default'} initialMode={id === 'replay' || id === 'live' ? id : undefined} />
  else if (route === 'showcase' && id && isTeacher) screen = <ShowcaseCase key={id} id={id} />
  else if (route === 'report' && id) screen = <ReportView key={id + (version || '')} runId={id} versionId={version}
    review={isTeacher ? (report, locate, refresh) => <ReviewForm key={report.id} report={report} locate={locate} refresh={refresh} /> : undefined} />
  else if (route === 'session' && id) screen = <SessionView key={id + (version ?? '') + (reportVersion ?? '')} id={id}
    selectedReport={version && reportVersion ? { runId: version, reportId: reportVersion } : undefined}
    review={isTeacher ? (report, locate, refresh) => <ReviewForm key={report.id} report={report} locate={locate} refresh={refresh} stayOnSession /> : undefined} />
  else if (route === 'live') screen = id ? <Workbench key={id} id={id} /> : <LiveTasks />
  else if (route === 'reviews' && isTeacher) screen = <ReviewQueue />
  else if (route === 'analytics' && isTeacher) screen = <Analytics />
  else if (route === 'settings' && admin) screen = <Settings />
  else if (route === 'tasks' && isTeacher) screen = <TaskManagement admin={admin} />
  else if (!route) screen = <Home user={user} />
  else screen = <Empty>该页面不可访问。</Empty>
  return <div className="shell">
    <aside className="sidebar">
      <a href="#/" className="brand"><span className="brand-icon" aria-hidden="true">◎</span><span>CoSolve Lens<small>AI 协同问题解决评价</small></span></a>
      <nav aria-label="主要导航">{links.map(([key, label]) => <a key={key} href={'#/' + key}
        aria-current={(route || '') === key ? 'page' : undefined}>{label}</a>)}</nav>
      <div className="account"><div><b>{user.display_name}</b><br /><small>{roles[user.role]}</small></div>
        <button className="sidebar-button" onClick={logout}>退出</button></div>
    </aside>
    <main className="workspace"><ErrorNotice text={error} />{screen}</main>
  </div>
}

function Home({ user }: { user: User }) {
  const readiness = useData<{ status: string }>('/readiness')
  const todo = useData<{ total: number }> (user.role === 'student' ? '/sessions?source_type=live&page_size=1' : '/reviews/queue?page_size=1')
  const datasets = useData<{ total: number }>(user.role === 'student' ? null : '/datasets?page_size=1')
  return <>
    <Heading title={'欢迎，' + user.display_name} />
    <ErrorNotice text={readiness.error || todo.error || datasets.error} />
    <div className="grid two">
      <section className="panel stack"><h2>待办</h2>
        {user.role === 'student' ? <a href="#/live">查看任务和最近 {todo.data?.total ?? '…'} 次尝试</a>
          : <><a href="#/reviews">{todo.data?.total ?? '…'} 条报告待复核</a>
            <a href="#/datasets">查看 {datasets.data?.total ?? '…'} 个数据源</a><a href="#/tasks">管理任务草稿</a></>}
      </section>
      <section className="panel stack"><h2>系统状态</h2>
        {readiness.loading && <Busy />}
        {readiness.data?.status === 'ready' && <Badge>数据库已就绪</Badge>}
        <p>真人任务使用静态演示运行。评价报告会标记证据范围和提供器。</p>
      </section>
    </div>
  </>
}

createRoot(document.getElementById('root')!).render(<App />)
