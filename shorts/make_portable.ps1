<#
  포터블 묶음 만들기 (Windows 전용, 한 번만 실행)

  Python 과 ffmpeg 를 폴더 안에 통째로 넣어, 아무것도 설치되지 않은
  컴퓨터에서도 압축만 풀면 바로 쓸 수 있는 폴더를 만듭니다.

      powershell -ExecutionPolicy Bypass -File make_portable.ps1

  결과: ..\shorts-portable\  (그리고 shorts-portable.zip)
#>
param(
  [string]$PythonVersion = "3.12.8",
  [string]$OutDir = "",
  [switch]$NoZip
)

$ErrorActionPreference = "Stop"
$src = $PSScriptRoot
if (-not $OutDir) { $OutDir = Join-Path (Split-Path $src -Parent) "shorts-portable" }
$work = Join-Path $env:TEMP "shorts-portable-build"

function Say($m) { Write-Host "  $m" -ForegroundColor Cyan }
function Fail($m) { Write-Host "  [실패] $m" -ForegroundColor Red; exit 1 }

Write-Host ""
Say "포터블 묶음을 만듭니다. 인터넷에서 약 120MB 를 받습니다."
Write-Host ""

New-Item -ItemType Directory -Force -Path $work, $OutDir | Out-Null
$rt = Join-Path $OutDir "runtime"
New-Item -ItemType Directory -Force -Path $rt | Out-Null

# ── 1. Python 임베디드 ─────────────────────────────────────────
Say "1/5  Python $PythonVersion 내려받는 중..."
$pyZip = Join-Path $work "python-embed.zip"
$pyUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
try { Invoke-WebRequest -Uri $pyUrl -OutFile $pyZip -UseBasicParsing }
catch { Fail "Python 내려받기 실패: $pyUrl`n       버전을 바꿔보세요: -PythonVersion 3.12.7" }

$pyDir = Join-Path $rt "python"
Remove-Item -Recurse -Force $pyDir -ErrorAction SilentlyContinue
Expand-Archive -Path $pyZip -DestinationPath $pyDir -Force

# 임베디드 Python 은 기본적으로 site-packages 를 안 읽으므로 열어준다
$pth = Get-ChildItem $pyDir -Filter "python*._pth" | Select-Object -First 1
if (-not $pth) { Fail "._pth 파일을 찾지 못했습니다." }
$lines = Get-Content $pth.FullName | ForEach-Object { $_ -replace '^#\s*import site', 'import site' }
if ($lines -notcontains "Lib\site-packages") { $lines += "Lib\site-packages" }
Set-Content -Path $pth.FullName -Value $lines -Encoding ASCII

# ── 2. pip ─────────────────────────────────────────────────────
Say "2/5  pip 설치 중..."
$getPip = Join-Path $work "get-pip.py"
try { Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $getPip -UseBasicParsing }
catch { Fail "get-pip.py 내려받기 실패" }
& "$pyDir\python.exe" $getPip --no-warn-script-location 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "pip 설치 실패" }

# ── 3. 라이브러리 ──────────────────────────────────────────────
Say "3/5  라이브러리 설치 중..."
& "$pyDir\python.exe" -m pip install --no-warn-script-location -r (Join-Path $src "requirements.txt") 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "라이브러리 설치 실패" }

# ── 4. ffmpeg ──────────────────────────────────────────────────
Say "4/5  ffmpeg 내려받는 중... (가장 오래 걸립니다)"
$ffZip = Join-Path $work "ffmpeg.zip"
try { Invoke-WebRequest -Uri "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" -OutFile $ffZip -UseBasicParsing }
catch { Fail "ffmpeg 내려받기 실패. https://www.gyan.dev/ffmpeg/builds/ 에서 직접 받아 runtime\ffmpeg 에 ffmpeg.exe, ffprobe.exe 를 넣으세요." }

$ffTmp = Join-Path $work "ff"
Remove-Item -Recurse -Force $ffTmp -ErrorAction SilentlyContinue
Expand-Archive -Path $ffZip -DestinationPath $ffTmp -Force
$ffDir = Join-Path $rt "ffmpeg"
New-Item -ItemType Directory -Force -Path $ffDir | Out-Null
foreach ($exe in @("ffmpeg.exe", "ffprobe.exe")) {
  $found = Get-ChildItem $ffTmp -Recurse -Filter $exe | Select-Object -First 1
  if (-not $found) { Fail "$exe 를 찾지 못했습니다." }
  Copy-Item $found.FullName (Join-Path $ffDir $exe) -Force
}

# ── 5. 프로그램 복사 ───────────────────────────────────────────
Say "5/5  프로그램 복사 중..."
foreach ($f in @("app.py", "build.py", "bgm.py", "requirements.txt", "README.md", "config.example.yaml")) {
  Copy-Item (Join-Path $src $f) $OutDir -Force
}
Remove-Item (Join-Path $OutDir "static") -Recurse -Force -ErrorAction SilentlyContinue
Copy-Item (Join-Path $src "static") $OutDir -Recurse -Force
New-Item -ItemType Directory -Force -Path (Join-Path $OutDir "clips"), (Join-Path $OutDir "fonts") | Out-Null

# 폰트가 원본에 있으면 같이 넣는다
$fonts = Get-ChildItem (Join-Path $src "fonts\*") -Include *.otf, *.ttf -File -ErrorAction SilentlyContinue
if ($fonts) { $fonts | Copy-Item -Destination (Join-Path $OutDir "fonts") -Force }

# 실행 파일 — 설치된 Python·ffmpeg 를 쓰지 않고 폴더 안의 것을 쓴다
@'
@echo off
setlocal
chcp 65001 >nul 2>nul
cd /d "%~dp0"
set "PATH=%~dp0runtime\ffmpeg;%PATH%"
"%~dp0runtime\python\python.exe" app.py %*
echo.
pause
'@ | Set-Content -Path (Join-Path $OutDir "실행.bat") -Encoding ASCII   # BOM 이 붙으면 cmd 가 첫 줄을 못 읽는다

@'
이 폴더는 설치가 필요 없습니다.

  실행.bat  을 더블클릭하면 대시보드가 열립니다.

폰에서도 쓰려면 명령 프롬프트에서:
  실행.bat --lan

영상은 clips 폴더에 넣거나, 대시보드 화면에 끌어다 놓으세요.
자막 폰트를 바꾸려면 .otf 파일을 fonts 폴더에 넣으세요.
'@ | Set-Content -Path (Join-Path $OutDir "읽어보세요.txt") -Encoding UTF8

Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue

$size = [math]::Round(((Get-ChildItem $OutDir -Recurse -File | Measure-Object Length -Sum).Sum / 1MB), 0)
Write-Host ""
Say "완성: $OutDir  (약 ${size}MB)"

if (-not $NoZip) {
  $zip = "$OutDir.zip"
  Say "압축하는 중..."
  Remove-Item $zip -ErrorAction SilentlyContinue
  Compress-Archive -Path "$OutDir\*" -DestinationPath $zip -CompressionLevel Optimal
  $zs = [math]::Round(((Get-Item $zip).Length / 1MB), 0)
  Say "압축 완료: $zip  (약 ${zs}MB)"
}

Write-Host ""
Say "이 폴더(또는 zip)를 클라우드에 올려두고, 다른 컴퓨터에서 풀어 실행.bat 을 누르면 됩니다."
Write-Host ""
