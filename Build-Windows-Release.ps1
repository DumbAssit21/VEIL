$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $project
Write-Host "Building VEIL Windows beta preview"
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Python 3 is required." }
}
$python = ".venv\Scripts\python.exe"
& $python -m pip install -r requirements.txt 'pyinstaller>=6,<7'
if ($LASTEXITCODE -ne 0) { throw "Python dependencies failed." }
& $python -m unittest discover -s tests -p 'test_*.py' -v
if ($LASTEXITCODE -ne 0) { throw "Tests failed. Refusing installer build." }
& $python -m PyInstaller --noconfirm --clean --windowed --onedir --name VEIL `
    --icon assets\VEIL.ico --add-data 'assets\VEIL.ico;assets' `
    --collect-all cv2 --collect-all tkinterdnd2 --collect-all av --collect-all sounddevice main.py
if ($LASTEXITCODE -ne 0 -or -not (Test-Path 'dist\VEIL\VEIL.exe')) { throw 'PyInstaller failed.' }
$iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) { $iscc = "$env:ProgramFiles\Inno Setup 6\ISCC.exe" }
if (-not (Test-Path $iscc)) { throw 'Inno Setup 6 is required: https://jrsoftware.org/isdl.php' }
& $iscc 'setup\VEIL.iss'
if ($LASTEXITCODE -ne 0) { throw 'Inno Setup failed.' }
$installer = Get-ChildItem 'release\VEIL-Setup-*.exe' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $installer) { throw 'Installer missing.' }
$hash = (Get-FileHash $installer.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
$sha = "$hash *$($installer.Name)"
$shaFile = Join-Path $installer.DirectoryName ($installer.Name + '.sha256')
[IO.File]::WriteAllText($shaFile, $sha + "`n", [Text.Encoding]::ASCII)
Write-Host "Installer: $($installer.FullName)"
Write-Host "SHA256: $hash"
Write-Warning 'This build is UNSIGNED and NOT independently security audited. For experimental use only.'
