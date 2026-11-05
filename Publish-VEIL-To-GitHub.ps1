# ASCII-only script so Windows PowerShell 5.1 parses it without UTF-8 BOM issues.
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$repo = "DumbAssit21/VEIL"
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $project

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { throw "Install GitHub CLI (https://cli.github.com/) and try again." }
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "Git for Windows is required." }
& gh auth status
if ($LASTEXITCODE -ne 0) { throw "Run gh auth login first." }
& gh auth setup-git
if ($LASTEXITCODE -ne 0) { throw "Could not configure GitHub Git authentication." }
$current = (& gh api user --jq .login | Select-Object -First 1).Trim()
if ($current -ne "DumbAssit21") { throw "Signed in as $current, not DumbAssit21. Refusing to publish." }

# Query available repositories without relying on an expected 404 error.
$repoNames = @(& gh repo list DumbAssit21 --limit 1000 --json name --jq ".[].name")
if ($LASTEXITCODE -ne 0) { throw "Unable to check existing GitHub repositories." }
if ($repoNames -notcontains "VEIL") {
    Write-Host "Creating PUBLIC repository $repo..."
    & gh repo create $repo --public --description "VEIL - experimental offline encrypted vault for Windows" --disable-wiki
    if ($LASTEXITCODE -ne 0) { throw "GitHub repository creation failed." }
} else {
    Write-Host "Repository $repo already exists; reusing it."
}

if (-not (Test-Path ".git")) {
    & git init -b main
    if ($LASTEXITCODE -ne 0) { throw "git init failed." }
}
& git add .
if ($LASTEXITCODE -ne 0) { throw "git add failed." }
# Refuse to publish obvious private vault files or key material.
$staged = @(& git diff --cached --name-only)
$bad = @($staged | Where-Object { $_ -match '(^|/)(VEIL_Vault[^/]*|vault\.json|manifest\.enc|state\.json|.*\.cap|.*\.evt|.*\.key|.*\.pem)$' })
if ($bad.Count -gt 0) { throw "Sensitive vault data staged; refusing to push: $($bad -join ', ')" }
& git diff --cached --quiet
if ($LASTEXITCODE -eq 1) {
    & git commit -m "Prepare VEIL public experimental preview and Windows installer CI"
    if ($LASTEXITCODE -ne 0) { throw "git commit failed. Configure your Git username/email first." }
} elseif ($LASTEXITCODE -ne 0) {
    throw "git diff failed."
}

$remotes = @(& git remote)
if ($LASTEXITCODE -ne 0) { throw "Could not inspect Git remotes." }
if ($remotes -notcontains "origin") {
    & git remote add origin "https://github.com/$repo.git"
    if ($LASTEXITCODE -ne 0) { throw "Could not configure Git origin." }
}
$origin = (& git remote get-url origin | Select-Object -First 1)
if ($LASTEXITCODE -ne 0 -or $origin -notmatch 'DumbAssit21/VEIL(\.git)?$') {
    throw "Origin points somewhere unexpected: $origin"
}
& git branch -M main
if ($LASTEXITCODE -ne 0) { throw "Could not select main branch." }
& git push -u origin main
if ($LASTEXITCODE -ne 0) { throw "Source push failed. Check your GitHub permissions and remote." }
Write-Host "VEIL source published at https://github.com/$repo"

$version = "v0.10.0-beta.1"
$existingTag = & git tag -l $version
if ($LASTEXITCODE -ne 0) { throw "Could not inspect version tags." }
if (-not $existingTag) {
    & git tag $version
    if ($LASTEXITCODE -ne 0) { throw "Could not create the prerelease tag." }
}
& git push origin $version
if ($LASTEXITCODE -ne 0) { throw "Could not push prerelease tag." }
Write-Host "Check https://github.com/$repo/actions and https://github.com/$repo/releases"
Write-Warning "GitHub will create a DRAFT prerelease. This is NOT independently security-audited software. Test before publishing it."
