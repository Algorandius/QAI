# ============================================================
#  NapCat「重复登录」修复工具
#  用法：双击同目录的 napcat-修复.bat
#        或在本文件上右键 →「使用 PowerShell 运行」
# ============================================================
$ErrorActionPreference = 'SilentlyContinue'
try { $Host.UI.RawUI.WindowTitle = 'NapCat 重复登录修复' } catch {}

function Show-Processes {
    Write-Host "`n===== 1. 相关进程 =====" -ForegroundColor Cyan
    $rows = Get-CimInstance Win32_Process |
        Where-Object { $_.Name -match '^(QQ|QQNT|QQEX|QQProtect|node)\.exe$' } |
        ForEach-Object {
            $c = $_.CommandLine
            if ($c -and $c.Length -gt 90) { $c = $c.Substring(0, 90) + '...' }
            [pscustomobject]@{ PID = $_.ProcessId; 进程 = $_.Name; 命令行 = $c }
        }
    if (-not $rows) {
        Write-Host "  （没有找到 QQ 或 node 进程）" -ForegroundColor Yellow
    } else {
        $rows | Format-Table -AutoSize -Wrap
    }
}

function Show-Ports {
    Write-Host "`n===== 2. 端口占用 =====" -ForegroundColor Cyan
    foreach ($p in 8080, 6099) {
        $c = Get-NetTCPConnection -LocalPort $p -State Listen
        if ($c) {
            $c | ForEach-Object {
                $name = (Get-Process -Id $_.OwningProcess).ProcessName
                Write-Host ("  {0} 被 PID {1}（{2}）占用" -f $p, $_.OwningProcess, $name)
            }
        } else {
            Write-Host ("  {0} 空闲" -f $p) -ForegroundColor DarkGray
        }
    }
}

function Get-NapCatNodes {
    Get-CimInstance Win32_Process -Filter "Name='node.exe'" |
        Where-Object { $_.CommandLine -and $_.CommandLine -match 'napcat|NapCat' }
}

# 只取 QQ 的「主进程」：Electron 的子进程都带 --type=xxx，主进程没有。
# 直接杀全部 QQ.exe 也行（子进程会跟着退），但先分清主次更安全。
function Get-QQMainProcs {
    Get-CimInstance Win32_Process -Filter "Name='QQ.exe' OR Name='QQNT.exe'" |
        Where-Object { -not $_.CommandLine -or $_.CommandLine -notmatch '--type=' }
}

function Get-QQProcs {
    Get-Process -Name QQ, QQNT, QQEX, QQProtect -ErrorAction SilentlyContinue
}

# ---------------- 主流程 ----------------
Clear-Host
Write-Host "==============================================" -ForegroundColor Green
Write-Host "   NapCat 重复登录 修复工具" -ForegroundColor Green
Write-Host "==============================================" -ForegroundColor Green

Show-Processes
Show-Ports

$nodes = @(Get-NapCatNodes)
$qqs   = @(Get-QQProcs)
$qmain = @(Get-QQMainProcs)

Write-Host "`n===== 3. 判断 =====" -ForegroundColor Cyan
Write-Host ("  NapCat 的 node 进程 ：{0} 个" -f $nodes.Count)
Write-Host ("  QQ 主进程           ：{0} 个" -f $qmain.Count)
Write-Host ("  QQ 全部进程（含子进程）：{0} 个" -f $qqs.Count) -ForegroundColor DarkGray

Write-Host "`n  说明：QQNT 是 Electron 架构，一个 QQ 开 6~10 个进程是正常的，"
Write-Host "        --type=gpu-process / renderer / utility 都是子进程，不是残留。" -ForegroundColor DarkGray

if ($nodes.Count -gt 1) {
    Write-Host "`n  → 有多个 NapCat 实例在跑，这就是「重复登录」最常见的原因" -ForegroundColor Yellow
}
if ($qmain.Count -gt 1) {
    Write-Host ("`n  → 有 {0} 个 QQ 主进程，说明开了多个 QQ 实例或上次没退干净" -f $qmain.Count) -ForegroundColor Yellow
}
if ($qqs.Count -gt 0 -and $nodes.Count -eq 0) {
    Write-Host "`n  → 没有 node 进程但有 QQ：说明你用的是 QQNT 版 NapCat，它跑在 QQ 进程里" -ForegroundColor Yellow
    Write-Host "    这种情况下必须走第 3 项（彻底清理），NapCat 会一起被杀" -ForegroundColor Yellow
}

Write-Host "`n===== 4. 选择处理方式 =====" -ForegroundColor Cyan
Write-Host "  1  只杀掉多余的 NapCat（保留一个）—— 最安全"
Write-Host "  2  杀掉全部 NapCat 的 node 进程"
Write-Host "  3  彻底清理：NapCat + 所有 QQ 进程（NapCat 会被一起杀掉，需重新启动）"
Write-Host "  0  只看不杀，退出"
$sel = Read-Host "`n请输入 0/1/2/3"

switch ($sel) {
    '1' {
        $keep = $nodes | Sort-Object CreationDate | Select-Object -Last 1
        $kill = $nodes | Where-Object { $_.ProcessId -ne $keep.ProcessId }
        if (-not $kill) {
            Write-Host "只有一个 NapCat 实例，无需处理。" -ForegroundColor Yellow
        } else {
            foreach ($p in $kill) {
                Stop-Process -Id $p.ProcessId -Force
                Write-Host ("  已结束 PID {0}" -f $p.ProcessId) -ForegroundColor Green
            }
            Write-Host ("保留 PID {0}" -f $keep.ProcessId)
        }
    }
    '2' {
        foreach ($p in $nodes) {
            Stop-Process -Id $p.ProcessId -Force
            Write-Host ("  已结束 NapCat PID {0}" -f $p.ProcessId) -ForegroundColor Green
        }
        if (-not $nodes) { Write-Host "没有 NapCat 进程。" -ForegroundColor Yellow }
    }
    '3' {
        Write-Host "`n即将结束：所有 node 进程（含 NapCat）+ QQ / QQNT / QQProtect" -ForegroundColor Red
        $ok = Read-Host "确认？输入 y 继续"
        if ($ok -eq 'y') {
            foreach ($p in $nodes) {
                Stop-Process -Id $p.ProcessId -Force
                Write-Host ("  已结束 NapCat PID {0}" -f $p.ProcessId) -ForegroundColor Green
            }
            foreach ($q in $qqs) {
                Stop-Process -Id $q.Id -Force
                Write-Host ("  已结束 {0} PID {1}" -f $q.ProcessName, $q.Id) -ForegroundColor Green
            }
        } else {
            Write-Host "已取消。"
        }
    }
    default {
        Write-Host "只看不杀，退出。"
    }
}

Write-Host "`n===== 5. 下一步 =====" -ForegroundColor Cyan
Write-Host "  · 如果是 QQNT 版 NapCat：重新双击 NapCat 启动器，等 QQ 登录"
Write-Host "  · 如果还提示重复登录：说明账号在手机或另一台电脑上还挂着"
Write-Host "    去 QQ 手机端 →「设置」→「账号安全」→「登录设备管理」→ 踢掉其他设备"
Write-Host ""
Read-Host "按回车退出"
