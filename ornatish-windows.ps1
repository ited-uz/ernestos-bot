# ErnestOS web — o'rnatish va ishga tushirish (Windows).
# "ornatish-windows.bat" ni ikki marta bosing; u shu faylni ishga tushiradi.
#
#   1. Dasturni  Documents\PROJECTS\ernestOS web  papkasiga ko'chiradi
#      (u yerda bo'lsa — joyida qoladi; ma'lumotlar, .venv va sozlamalar
#      hech qachon ustidan yozilmaydi).
#   2. Python 3.11+ ni topadi; yo'q bo'lsa winget bilan o'rnatadi.
#   3. Ish stoliga "ErnestOS" yorlig'ini qo'yadi.
#   4. Serverni ishga tushiradi va brauzerda http://localhost:8000/app/ ni ochadi.

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}

function Say($text) { Write-Host $text }
function Stop-With($text) {
    Write-Host ""
    Write-Host $text -ForegroundColor Yellow
    Read-Host "Yopish uchun Enter"
    exit 1
}

$src = (Resolve-Path (Split-Path -Parent $MyInvocation.MyCommand.Path)).Path.TrimEnd('\')
$docs = [Environment]::GetFolderPath('MyDocuments')
if (-not $docs) { $docs = Join-Path $HOME 'Documents' }   # never empty, whatever the system says
# The folder is called PROJECTS; an existing PROFECTS (same idea) is used as it is.
$projects = Join-Path $docs 'PROJECTS'
if (-not (Test-Path $projects) -and (Test-Path (Join-Path $docs 'PROFECTS'))) { $projects = Join-Path $docs 'PROFECTS' }
$dest = Join-Path $projects 'ernestOS web'

Say ""
Say "  ErnestOS web"
Say "  Papka: $dest"
Say ""

# --- 1. the files ------------------------------------------------------------
if ($src -ne $dest.TrimEnd('\')) {
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    # Your data (data\), the installed packages (.venv) and your settings
    # (.env.local) in the destination are never touched by an update.
    & robocopy $src $dest /E /XD .venv data __pycache__ /XF .env.local /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { Stop-With "Fayllarni ko'chirib bo'lmadi (robocopy $LASTEXITCODE)." }
    Say "  • Fayllar ko'chirildi"
} else {
    Say "  • Dastur allaqachon shu papkada"
}

# --- 2. Python ---------------------------------------------------------------
function Find-Python {
    $check = "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
    $candidates = @(@('py', '-3'), @('python'), @('python3'))
    foreach ($c in $candidates) {
        $exe = $c[0]
        $rest = @()
        if ($c.Count -gt 1) { $rest = $c[1..($c.Count - 1)] }
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        try {
            & $exe @rest -c $check 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) { return , $c }
        } catch {}
    }
    # Installed just now, before PATH knows about it.
    $found = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue |
             Sort-Object FullName -Descending
    foreach ($p in $found) {
        try {
            & $p.FullName -c $check 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) { return , @($p.FullName) }
        } catch {}
    }
    return $null
}

$py = Find-Python
if (-not $py) {
    Say "  • Python 3.11+ topilmadi — o'rnatilmoqda (winget, 1–3 daqiqa)…"
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        & winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
        $py = Find-Python
    }
    if (-not $py) {
        Start-Process "https://www.python.org/downloads/windows/"
        Stop-With ("Python'ni o'rnating (python.org ochildi): o'rnatishda 'Add python.exe to PATH' belgisini qo'ying.`n" +
                   "Keyin 'ornatish-windows.bat' ni qayta ishga tushiring.")
    }
}
$pyExe = $py[0]
$pyArgs = @()
if ($py.Count -gt 1) { $pyArgs = $py[1..($py.Count - 1)] }
$version = & $pyExe @pyArgs -c "import sys; print('%d.%d.%d' % sys.version_info[:3])"
Say "  • Python $version"

# --- 3. the desktop shortcut ------------------------------------------------
try {
    $lnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'ErnestOS.lnk'
    $shell = New-Object -ComObject WScript.Shell
    $s = $shell.CreateShortcut($lnk)
    $s.TargetPath = Join-Path $dest 'start-local.bat'
    $s.WorkingDirectory = $dest
    $s.IconLocation = (Join-Path $dest 'webapp\icons\ernestos.ico')
    $s.Description = 'ErnestOS — shu kompyuterda'
    $s.Save()
    Say "  • Ish stolida 'ErnestOS' yorlig'i — keyingi safar shuni bosing"
} catch {
    Say "  • Yorliq yaratilmadi (muhim emas): keyingi safar papkadagi start-local.bat ni bosing"
}

# --- 4. start --------------------------------------------------------------
Say ""
Set-Location $dest
& $pyExe @pyArgs run_local.py
if ($LASTEXITCODE -ne 0) { Stop-With "Server to'xtadi (kod $LASTEXITCODE). Yuqoridagi xabarni o'qing." }
