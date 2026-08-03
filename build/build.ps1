<#
    Builds the Windows distribution.

        powershell -ExecutionPolicy Bypass -File build\build.ps1

    Result: dist\EnbridgeMDR\EnbridgeMDR.exe (GUI) and EnbridgeMDR-cli.exe.

    Pass -BundleTesseract to copy an installed Tesseract into vendor\ first,
    so the built folder runs on a machine with nothing pre-installed.
#>
[CmdletBinding()]
param(
    [switch]$BundleTesseract,
    [string]$TesseractDir = "C:\Program Files\Tesseract-OCR",
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $PSScriptRoot
Set-Location $project

if ($Clean) {
    Write-Host "Cleaning previous build output..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force build\pyinstaller, dist -ErrorAction SilentlyContinue
}

Write-Host "Creating virtual environment..." -ForegroundColor Cyan
if (-not (Test-Path .venv)) { python -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip --quiet
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt --quiet
& .\.venv\Scripts\python.exe -m pip install pyinstaller --quiet

if ($BundleTesseract) {
    if (-not (Test-Path $TesseractDir)) {
        throw "Tesseract not found at $TesseractDir. Install it, or pass -TesseractDir."
    }
    Write-Host "Bundling Tesseract from $TesseractDir..." -ForegroundColor Cyan
    $vendor = Join-Path $project "vendor\tesseract"
    New-Item -ItemType Directory -Force -Path $vendor | Out-Null
    Copy-Item (Join-Path $TesseractDir "tesseract.exe") $vendor -Force
    Copy-Item (Join-Path $TesseractDir "*.dll") $vendor -Force -ErrorAction SilentlyContinue
    $tessdata = Join-Path $vendor "tessdata"
    New-Item -ItemType Directory -Force -Path $tessdata | Out-Null
    # Only the English model is needed; copying every language adds ~1 GB.
    Copy-Item (Join-Path $TesseractDir "tessdata\eng.traineddata") $tessdata -Force
    Copy-Item (Join-Path $TesseractDir "tessdata\osd.traineddata") $tessdata -Force -ErrorAction SilentlyContinue
}

Write-Host "Running PyInstaller..." -ForegroundColor Cyan
& .\.venv\Scripts\pyinstaller.exe --noconfirm --workpath build\pyinstaller --distpath dist build\MDR.spec

$out = Join-Path $project "dist\EnbridgeMDR\EnbridgeMDR.exe"
if (Test-Path $out) {
    Write-Host "`nBuild complete: $out" -ForegroundColor Green
    Write-Host "Distribute the whole dist\EnbridgeMDR folder, not just the .exe." -ForegroundColor Yellow
} else {
    throw "Build finished but $out was not produced."
}
