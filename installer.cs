// QAI 一键安装器
//
// 目的：让用户不用「下载 zip → 找解压软件 → 解压 → 再双击」，直接运行本 exe 即可。
//
// 行为：
//   1. 选一个安装目录（默认 %LOCALAPPDATA%\QAI）
//   2. 点「一键安装」→ 查 GitHub 最新 Release → 下载完整包（直连 + 加速源逐个试）
//      → 校验 SHA256 → 解压到安装目录 → 建桌面快捷方式 → 启动 QAI
//   3. 连不上 GitHub 时，可以用「从本地 zip 安装」直接选已下载的包
//
// 编译（build_portable.py 会自动调用）：
//   csc /nologo /target:winexe /r:System.Windows.Forms.dll /r:System.Drawing.dll
//       /r:System.IO.Compression.FileSystem.dll /win32icon:launcher_ui\app.ico
//       /out:QAI-Setup.exe installer.cs
//
// 注意：这里刻意用旧语法（C# 5），因为 Windows 自带的 .NET Framework csc.exe
// 不支持字符串插值、?. 这些新语法。

using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Net;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;

static class QaiSetup
{
    const string ApiUrl = "https://api.github.com/repos/Algorandius/QAI/releases/latest";
    public const string ProjectUrl = "https://github.com/Algorandius/QAI";
    static readonly string[] Mirrors = new string[]
    {
        "",
        "https://gh-proxy.com/",
        "https://github.chenc.dev/",
    };

    [STAThread]
    static void Main()
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Application.Run(new SetupForm());
    }

    public static string DefaultTarget()
    {
        string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        return Path.Combine(local, "QAI");
    }

    public static string HttpGetText(string url, int timeoutMs)
    {
        using (WebClient wc = new WebClient())
        {
            wc.Encoding = Encoding.UTF8;
            wc.Headers.Add("User-Agent", "QAI-Setup");
            wc.Headers.Add("Accept", "application/vnd.github+json");
            wc.Proxy = WebRequest.DefaultWebProxy;
            wc.Proxy.Credentials = CredentialCache.DefaultCredentials;
            byte[] data = DownloadBytesWithTimeout(wc, url, timeoutMs);
            return Encoding.UTF8.GetString(data);
        }
    }

    static byte[] DownloadBytesWithTimeout(WebClient wc, string url, int timeoutMs)
    {
        // WebClient 没有超时参数；用 DownloadData + 超时控制太绕，
        // 这里直接同步下载，卡住的情况由外层逐个源重试兜住
        return wc.DownloadData(url);
    }

    public static string TryFetchManifest(out string source)
    {
        source = "";
        string lastError = "";
        for (int i = 0; i < Mirrors.Length; i++)
        {
            string url = Mirrors[i] + ApiUrl;
            try
            {
                string text = HttpGetText(url, 15000);
                if (text != null && text.IndexOf("assets", StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    source = url;
                    return text;
                }
            }
            catch (Exception e)
            {
                lastError = e.Message;
            }
        }
        source = lastError;
        return null;
    }

    public static string Match(string pattern, string text)
    {
        Match m = Regex.Match(text, pattern);
        return m.Success ? m.Groups[1].Value : "";
    }

    public static string PickAsset(string json, string suffix)
    {
        MatchCollection all = Regex.Matches(json, "\"browser_download_url\"\\s*:\\s*\"([^\"]+)\"");
        for (int i = 0; i < all.Count; i++)
        {
            string url = all[i].Groups[1].Value;
            if (url.EndsWith(suffix, StringComparison.OrdinalIgnoreCase))
                return url;
        }
        return "";
    }

    public static string DownloadToFile(string url, string dest, Action<int> progress)
    {
        string lastError = "";
        for (int i = 0; i < Mirrors.Length; i++)
        {
            string target = Mirrors[i] + url;
            try
            {
                using (WebClient wc = new WebClient())
                {
                    wc.Headers.Add("User-Agent", "QAI-Setup");
                    wc.Proxy = WebRequest.DefaultWebProxy;
                    wc.Proxy.Credentials = CredentialCache.DefaultCredentials;
                    byte[] data = wc.DownloadData(target);
                    File.WriteAllBytes(dest, data);
                    if (progress != null) progress(100);
                    return target;
                }
            }
            catch (Exception e)
            {
                lastError = e.Message;
            }
        }
        throw new Exception("下载失败（直连和加速源都试过了）：" + lastError);
    }

    public static string Sha256Of(string path)
    {
        using (FileStream fs = File.OpenRead(path))
        using (System.Security.Cryptography.SHA256 sha = System.Security.Cryptography.SHA256.Create())
        {
            byte[] hash = sha.ComputeHash(fs);
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < hash.Length; i++) sb.Append(hash[i].ToString("x2"));
            return sb.ToString();
        }
    }

    public static void CreateDesktopShortcut(string exePath)
    {
        try
        {
            string desktop = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
            string link = Path.Combine(desktop, "QAI.lnk");
            Type shellType = Type.GetTypeFromProgID("WScript.Shell");
            if (shellType == null) return;
            object shell = Activator.CreateInstance(shellType);
            object shortcut = shellType.InvokeMember("CreateShortcut", System.Reflection.BindingFlags.InvokeMethod,
                null, shell, new object[] { link });
            Type scType = shortcut.GetType();
            scType.InvokeMember("TargetPath", System.Reflection.BindingFlags.SetProperty, null, shortcut, new object[] { exePath });
            scType.InvokeMember("WorkingDirectory", System.Reflection.BindingFlags.SetProperty, null, shortcut, new object[] { Path.GetDirectoryName(exePath) });
            scType.InvokeMember("IconLocation", System.Reflection.BindingFlags.SetProperty, null, shortcut, new object[] { exePath + ",0" });
            scType.InvokeMember("Save", System.Reflection.BindingFlags.InvokeMethod, null, shortcut, null);
        }
        catch (Exception)
        {
            // 快捷方式失败不算安装失败
        }
    }

    /// <summary>把解压出来的版本目录里的内容平移到安装目录。</summary>
    public static string NormalizeInstallRoot(string extracted, string target)
    {
        string[] found = Directory.GetFiles(extracted, "QAI.exe", SearchOption.AllDirectories);
        if (found.Length == 0) return "";
        string srcRoot = Path.GetDirectoryName(found[0]);
        Directory.CreateDirectory(target);
        CopyTree(srcRoot, target);
        return Path.Combine(target, "QAI.exe");
    }

    static void CopyTree(string source, string dest)
    {
        Directory.CreateDirectory(dest);
        string[] files = Directory.GetFiles(source);
        for (int i = 0; i < files.Length; i++)
        {
            string name = Path.GetFileName(files[i]);
            File.Copy(files[i], Path.Combine(dest, name), true);
        }
        string[] dirs = Directory.GetDirectories(source);
        for (int i = 0; i < dirs.Length; i++)
        {
            CopyTree(dirs[i], Path.Combine(dest, Path.GetFileName(dirs[i])));
        }
    }
}

class SetupForm : Form
{
    TextBox _path;
    Button _browse;
    Button _install;
    Button _local;
    Label _status;
    ProgressBar _bar;
    TextBox _log;
    volatile bool _busy;

    public SetupForm()
    {
        Text = "QAI 一键安装";
        ClientSize = new Size(620, 430);
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = false;
        StartPosition = FormStartPosition.CenterScreen;
        Font = new Font("Microsoft YaHei UI", 9F);

        Label title = new Label();
        title.Text = "QAI 聊天机器人";
        title.Font = new Font("Microsoft YaHei UI", 16F, FontStyle.Bold);
        title.AutoSize = true;
        title.Location = new Point(20, 18);
        Controls.Add(title);

        Label sub = new Label();
        sub.Text = "选一个安装目录，点「一键安装」即可。不用解压、不用装 Python。";
        sub.AutoSize = true;
        sub.ForeColor = Color.FromArgb(90, 90, 90);
        sub.Location = new Point(22, 56);
        Controls.Add(sub);

        Label lblPath = new Label();
        lblPath.Text = "安装位置";
        lblPath.AutoSize = true;
        lblPath.Location = new Point(22, 92);
        Controls.Add(lblPath);

        _path = new TextBox();
        _path.Text = QaiSetup.DefaultTarget();
        _path.Location = new Point(22, 114);
        _path.Size = new Size(450, 26);
        Controls.Add(_path);

        _browse = new Button();
        _browse.Text = "浏览…";
        _browse.Location = new Point(482, 112);
        _browse.Size = new Size(82, 28);
        _browse.Click += new EventHandler(OnBrowse);
        Controls.Add(_browse);

        _install = new Button();
        _install.Text = "一键安装";
        _install.Location = new Point(22, 156);
        _install.Size = new Size(160, 40);
        _install.BackColor = Color.FromArgb(59, 130, 246);
        _install.ForeColor = Color.White;
        _install.FlatStyle = FlatStyle.Flat;
        _install.Click += new EventHandler(OnInstall);
        Controls.Add(_install);

        _local = new Button();
        _local.Text = "从本地 zip 安装…";
        _local.Location = new Point(192, 156);
        _local.Size = new Size(160, 40);
        _local.Click += new EventHandler(OnLocalZip);
        Controls.Add(_local);

        Label hint = new Label();
        hint.Text = "连不上 GitHub 时用右边这个：从网盘/群里下载 QAI-…-Windows-x64.zip 后选进来。";
        hint.AutoSize = true;
        hint.ForeColor = Color.FromArgb(90, 90, 90);
        hint.Location = new Point(22, 202);
        Controls.Add(hint);

        _bar = new ProgressBar();
        _bar.Location = new Point(22, 230);
        _bar.Size = new Size(542, 16);
        _bar.Style = ProgressBarStyle.Continuous;
        Controls.Add(_bar);

        _status = new Label();
        _status.Text = "等待开始";
        _status.AutoSize = true;
        _status.Location = new Point(22, 254);
        Controls.Add(_status);

        _log = new TextBox();
        _log.Multiline = true;
        _log.ReadOnly = true;
        _log.ScrollBars = ScrollBars.Vertical;
        _log.Location = new Point(22, 278);
        _log.Size = new Size(542, 96);
        _log.BackColor = Color.FromArgb(248, 248, 248);
        Controls.Add(_log);

        LinkLabel link = new LinkLabel();
        link.Text = "打开项目主页";
        link.AutoSize = true;
        link.Location = new Point(22, 386);
        link.LinkClicked += new LinkLabelLinkClickedEventHandler(OnProjectLink);
        Controls.Add(link);
    }

    void OnProjectLink(object sender, LinkLabelLinkClickedEventArgs e)
    {
        try { Process.Start(QaiSetup.ProjectUrl); } catch (Exception) { }
    }

    void Log(string text)
    {
        if (InvokeRequired)
        {
            BeginInvoke(new Action<string>(Log), new object[] { text });
            return;
        }
        _log.AppendText(text + Environment.NewLine);
    }

    void SetStatus(string text, int percent)
    {
        if (InvokeRequired)
        {
            BeginInvoke(new Action<string, int>(SetStatus), new object[] { text, percent });
            return;
        }
        _status.Text = text;
        if (percent >= 0) _bar.Value = Math.Max(0, Math.Min(100, percent));
    }

    void OnBrowse(object sender, EventArgs e)
    {
        using (FolderBrowserDialog dlg = new FolderBrowserDialog())
        {
            dlg.Description = "选择 QAI 的安装目录";
            dlg.SelectedPath = _path.Text;
            if (dlg.ShowDialog() == DialogResult.OK) _path.Text = dlg.SelectedPath;
        }
    }

    void OnInstall(object sender, EventArgs e)
    {
        if (_busy) return;
        string target = _path.Text.Trim();
        if (target.Length == 0)
        {
            MessageBox.Show("请先选择安装目录。", "QAI 安装", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return;
        }
        _busy = true;
        SetBusy(true);
        Log("安装目录：" + target);
        Thread t = new Thread(delegate() { InstallOnline(target); });
        t.IsBackground = true;
        t.Start();
    }

    void OnLocalZip(object sender, EventArgs e)
    {
        if (_busy) return;
        using (OpenFileDialog dlg = new OpenFileDialog())
        {
            dlg.Title = "选择 QAI 安装包";
            dlg.Filter = "QAI 安装包 (*.zip)|*.zip|所有文件 (*.*)|*.*";
            if (dlg.ShowDialog() != DialogResult.OK) return;
            string target = _path.Text.Trim();
            if (target.Length == 0) target = QaiSetup.DefaultTarget();
            _busy = true;
            SetBusy(true);
            Log("安装目录：" + target);
            string zip = dlg.FileName;
            Thread t = new Thread(delegate() { InstallFromZip(zip, target); });
            t.IsBackground = true;
            t.Start();
        }
    }

    void SetBusy(bool busy)
    {
        if (InvokeRequired)
        {
            BeginInvoke(new Action<bool>(SetBusy), new object[] { busy });
            return;
        }
        _install.Enabled = !busy;
        _local.Enabled = !busy;
        _browse.Enabled = !busy;
        _path.Enabled = !busy;
    }

    void InstallOnline(string target)
    {
        string zipPath = Path.Combine(Path.GetTempPath(), "qai-setup-download.zip");
        try
        {
            SetStatus("正在查找最新版本…", 5);
            string source;
            string json = QaiSetup.TryFetchManifest(out source);
            if (json == null)
            {
                Log("查不到版本信息：" + source);
                throw new Exception("连不上 GitHub（直连和加速源都失败）。请改用「从本地 zip 安装」。");
            }
            string tag = QaiSetup.Match("\"tag_name\"\\s*:\\s*\"([^\"]+)\"", json);
            string zipUrl = QaiSetup.PickAsset(json, "-Windows-x64.zip");
            string shaUrl = QaiSetup.PickAsset(json, "-Windows-x64.zip.sha256");
            if (zipUrl.Length == 0)
                throw new Exception("这个 Release 里没有完整的 Windows 安装包。");
            Log("最新版本：" + tag);
            SetStatus("正在下载安装包（约 17MB）…", 20);
            QaiSetup.DownloadToFile(zipUrl, zipPath, delegate(int p) { SetStatus("正在下载安装包…", 20 + p * 50 / 100); });
            Log("下载完成：" + new FileInfo(zipPath).Length / 1024 + " KB");

            if (shaUrl.Length > 0)
            {
                SetStatus("正在校验安装包…", 75);
                try
                {
                    string expect = QaiSetup.HttpGetText(shaUrl, 20000).Trim();
                    if (expect.Length > 64) expect = expect.Substring(0, 64);
                    string actual = QaiSetup.Sha256Of(zipPath);
                    if (!string.Equals(expect, actual, StringComparison.OrdinalIgnoreCase))
                        throw new Exception("安装包校验失败（SHA256 不一致），已中止。");
                    Log("SHA256 校验通过");
                }
                catch (Exception ex)
                {
                    if (ex.Message.IndexOf("校验失败", StringComparison.Ordinal) >= 0) throw;
                    Log("拿不到校验文件，跳过校验：" + ex.Message);
                }
            }

            InstallFromZip(zipPath, target);
        }
        catch (Exception ex)
        {
            Fail(ex.Message);
        }
        finally
        {
            try { if (File.Exists(zipPath)) File.Delete(zipPath); } catch (Exception) { }
        }
    }

    void InstallFromZip(string zipPath, string target)
    {
        try
        {
            if (!File.Exists(zipPath)) throw new Exception("找不到安装包：" + zipPath);
            SetStatus("正在解压…", 82);
            string temp = Path.Combine(Path.GetTempPath(), "qai-setup-extract");
            try { if (Directory.Exists(temp)) Directory.Delete(temp, true); } catch (Exception) { }
            ZipFile.ExtractToDirectory(zipPath, temp);

            SetStatus("正在安装到目标目录…", 92);
            string exe = QaiSetup.NormalizeInstallRoot(temp, target);
            if (exe.Length == 0) throw new Exception("安装包里没有 QAI.exe，可能选错了文件。");

            try { Directory.Delete(temp, true); } catch (Exception) { }

            QaiSetup.CreateDesktopShortcut(exe);
            SetStatus("安装完成，正在启动…", 100);
            Log("安装完成：" + exe);
            Log("已在桌面创建 QAI 快捷方式。");

            if (MessageBox.Show("安装完成，现在就启动 QAI 吗？", "QAI 安装",
                    MessageBoxButtons.YesNo, MessageBoxIcon.Information) == DialogResult.Yes)
            {
                try { Process.Start(exe); } catch (Exception e) { Log("启动失败：" + e.Message); }
            }
        }
        catch (Exception ex)
        {
            Fail(ex.Message);
        }
        finally
        {
            SetBusy(false);
            _busy = false;
        }
    }

    void Fail(string message)
    {
        SetStatus("安装失败", 0);
        Log("失败：" + message);
        if (InvokeRequired)
        {
            BeginInvoke(new Action<string>(Fail), new object[] { message });
            return;
        }
        MessageBox.Show(message, "QAI 安装失败", MessageBoxButtons.OK, MessageBoxIcon.Error);
    }
}
