using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Windows.Forms;

[assembly: System.Reflection.AssemblyProduct("CoSolve Lens")]
[assembly: System.Reflection.AssemblyVersion("1.0.0.0")]

internal static class Launcher
{
    private const int Port = 5188;
    private const string Origin = "http://127.0.0.1:5188";
#if TEACHER
    private const string Role = "teacher";
    private const string Title = "教师登录";
#elif STUDENT
    private const string Role = "student";
    private const string Title = "学生登录";
#elif ADMIN
    private const string Role = "admin";
    private const string Title = "管理员登录";
#else
#error The build must select a login role.
#endif

    [STAThread]
    private static int Main(string[] args)
    {
        bool check = args.Length == 1 && args[0] == "--check";
        bool stop = args.Length == 1 && args[0] == "--stop";
        string root = Path.GetFullPath(AppDomain.CurrentDomain.BaseDirectory);
        string instance;
        using (SHA256 sha = SHA256.Create())
        {
            byte[] location = Encoding.UTF8.GetBytes(root.TrimEnd(Path.DirectorySeparatorChar).ToUpperInvariant());
            instance = BitConverter.ToString(sha.ComputeHash(location)).Replace("-", "").ToLowerInvariant();
        }

        try
        {
            using (var mutex = new Mutex(false, @"Local\AICollabDesktop_" + instance))
            {
                bool held;
                try { held = mutex.WaitOne(TimeSpan.FromSeconds(30)); }
                catch (AbandonedMutexException) { held = true; }
                if (!held)
                    throw new InvalidOperationException("本地服务正在启动，请稍后双击重试。");
                try
                {
                    if (stop)
                        StopServer(root, instance);
                    else
                        EnsureServer(root, instance);
                }
                finally { mutex.ReleaseMutex(); }
            }
            if (!check && !stop)
                OpenRoleWindow(root);
            return 0;
        }
        catch (Exception error)
        {
            string details = error.Message;
            if (check || stop)
            {
                try
                {
                    string runtime = Path.Combine(root, "var", "desktop");
                    Directory.CreateDirectory(runtime);
                    File.AppendAllText(Path.Combine(runtime, "launcher.log"), details + Environment.NewLine, Encoding.UTF8);
                }
                catch (IOException) { }
                if (check)
                    return 1;
            }
            MessageBox.Show(details, Title, MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }

    private static void EnsureServer(string root, string instance)
    {
        string current = Probe("/api/desktop-instance");
        if (current != null)
        {
            VerifyInstance(current, instance);
            if (Probe("/api/readiness") == null)
                throw new InvalidOperationException("服务已启动，评价进程暂不可用。请先用启动器的 --stop 参数关闭，再重新启动。");
            return;
        }

        string python = Path.Combine(root, ".venv", "Scripts", "python.exe");
        string frontend = Path.Combine(root, "frontend", "dist", "index.html");
        if (!File.Exists(python) || !File.Exists(frontend))
            throw new InvalidOperationException("缺少程序依赖或前端文件。请从「系统实现」目录运行 scripts\\setup.ps1 和 scripts\\build-desktop.ps1。");
        using (Process service = Process.Start(new ProcessStartInfo
        {
            FileName = python,
            Arguments = "-m backend.app desktop " + Port + " " + instance,
            WorkingDirectory = root,
            UseShellExecute = false,
            CreateNoWindow = true,
        }))
        {
            var elapsed = Stopwatch.StartNew();
            while (elapsed.Elapsed < TimeSpan.FromSeconds(25))
            {
                current = Probe("/api/desktop-instance");
                if (current != null)
                {
                    VerifyInstance(current, instance);
                    if (Probe("/api/readiness") != null)
                        return;
                }
                if (service.HasExited)
                    break;
                Thread.Sleep(180);
            }
            throw new InvalidOperationException("本地服务启动失败。请查看 var\\desktop\\service.log，并检查端口 5188 是否被占用。");
        }
    }

    private static string Probe(string path)
    {
        var request = CreateRequest(path);
        try
        {
            using (var response = (HttpWebResponse)request.GetResponse())
            using (var reader = new StreamReader(response.GetResponseStream()))
                return reader.ReadToEnd();
        }
        catch (WebException error)
        {
            if (error.Response != null)
            {
                using (error.Response)
                    return path == "/api/desktop-instance" ? String.Empty : null;
            }
            return null;
        }
    }

    private static HttpWebRequest CreateRequest(string path)
    {
        var request = (HttpWebRequest)WebRequest.Create(Origin + path);
        request.Timeout = 1200;
        request.ReadWriteTimeout = 1200;
        request.AllowAutoRedirect = false;
        request.Proxy = null;
        return request;
    }

    private static void VerifyInstance(string actual, string expected)
    {
        if (actual != expected)
            throw new InvalidOperationException("端口 5188 已被其他实例占用。请先关闭使用该端口的本地服务。");
    }

    private static void StopServer(string root, string instance)
    {
        string current = Probe("/api/desktop-instance");
        if (current == null)
            return;
        VerifyInstance(current, instance);
        var request = CreateRequest("/api/desktop-shutdown");
        request.Method = "POST";
        request.ContentLength = 0;
        string keyFile = Path.Combine(root, "var", "desktop", "stop.key");
        request.Headers["x-desktop-token"] = File.ReadAllText(keyFile).Trim();
        using (request.GetResponse()) { }

        var elapsed = Stopwatch.StartNew();
        while (elapsed.Elapsed < TimeSpan.FromSeconds(8))
        {
            if (Probe("/api/desktop-instance") != instance && !File.Exists(keyFile))
                return;
            Thread.Sleep(180);
        }
        throw new InvalidOperationException("本地服务仍在关闭，后台任务可能尚未结束。");
    }

    private static void OpenRoleWindow(string root)
    {
        string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        string programFiles = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles);
        string programFilesX86 = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86);
        string[] candidates = {
            Path.Combine(programFilesX86, "Microsoft", "Edge", "Application", "msedge.exe"),
            Path.Combine(programFiles, "Microsoft", "Edge", "Application", "msedge.exe"),
            Path.Combine(local, "Microsoft", "Edge", "Application", "msedge.exe"),
            Path.Combine(programFiles, "Google", "Chrome", "Application", "chrome.exe"),
            Path.Combine(programFilesX86, "Google", "Chrome", "Application", "chrome.exe"),
            Path.Combine(local, "Google", "Chrome", "Application", "chrome.exe"),
        };
        string browser = Array.Find(candidates, File.Exists);
        if (browser == null)
            throw new InvalidOperationException("未找到 Microsoft Edge 或 Google Chrome。安装其中一个后再打开启动器。");
        string profile = Path.Combine(root, "var", "desktop", "browser-" + Role);
        Process.Start(new ProcessStartInfo
        {
            FileName = browser,
            Arguments = "--no-first-run --no-default-browser-check --user-data-dir=\"" + profile +
                        "\" --app=\"" + Origin + "/#/login/" + Role + "\"",
            WorkingDirectory = root,
            UseShellExecute = false,
        });
    }
}
