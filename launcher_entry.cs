using System;
using System.Diagnostics;
using System.IO;

class QqbotLauncher
{
    [STAThread]
    static void Main()
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string script = Path.Combine(root, "launcher.py");
        if (!File.Exists(script))
        {
            return;
        }
        string[] candidates = new string[]
        {
            Path.Combine(root, @"runtime\python\pythonw.exe"),
            Path.Combine(root, @".venv\Scripts\pythonw.exe")
        };
        string pythonw = null;
        foreach (string candidate in candidates)
        {
            if (File.Exists(candidate))
            {
                pythonw = candidate;
                break;
            }
        }
        if (pythonw == null)
        {
            return;
        }
        ProcessStartInfo psi = new ProcessStartInfo();
        psi.FileName = pythonw;
        psi.Arguments = "\"" + script + "\"";
        psi.WorkingDirectory = root;
        psi.UseShellExecute = false;
        psi.CreateNoWindow = true;
        Process.Start(psi);
    }
}
