# 파라다이스 도고 캐빈 예약 도우미 — Windows 설치 / 업데이트
#
# PowerShell 을 열고 아래 한 줄을 붙여넣으세요.
#
#   irm https://raw.githubusercontent.com/kimoonz/kimoonz/claude/paradise-dogo-cabin-booking-y5lryk/install.ps1 | iex
#
# 이미 설치돼 있으면 코드만 최신으로 바꾸고, 설정과 로그인 정보는 그대로 둡니다.

$ErrorActionPreference = 'Stop'

$Repo   = 'kimoonz/kimoonz'
$Branch = 'claude/paradise-dogo-cabin-booking-y5lryk'
$Dest   = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'paradogo'

function Say($msg) { Write-Host $msg }
function Die($msg) {
    Write-Host ''
    Write-Host "[막힘] $msg" -ForegroundColor Red
    Write-Host ''
    Read-Host '엔터를 누르면 창이 닫힙니다'
    exit 1
}

Say '=================================================='
Say ' 파라다이스 도고 캐빈 예약 도우미'
Say '=================================================='
Say ''

# 구버전 PowerShell 은 TLS 1.2 를 켜지 않으면 GitHub 에 붙지 못한다.
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch { }

$isUpdate = Test-Path $Dest
if ($isUpdate) {
    Say "이미 설치돼 있습니다: $Dest"
    Say '코드만 최신으로 바꾸고, 설정과 로그인 정보는 그대로 둡니다.'
} else {
    Say "새로 설치합니다: $Dest"
}
Say ''

# --- 내려받기 ---------------------------------------------------------------
$zipUrl = "https://codeload.github.com/$Repo/zip/refs/heads/$Branch"
$zip    = Join-Path $env:TEMP 'paradogo.zip'
$tmpDir = Join-Path $env:TEMP 'paradogo-unzip'

Say '코드를 내려받는 중...'
try {
    Invoke-WebRequest -Uri $zipUrl -OutFile $zip -UseBasicParsing
} catch {
    Die "코드를 내려받지 못했습니다. 인터넷 연결을 확인해 주세요.`n$($_.Exception.Message)"
}

if (Test-Path $tmpDir) { Remove-Item -Recurse -Force $tmpDir }
try {
    Expand-Archive -Path $zip -DestinationPath $tmpDir -Force
} catch {
    Die "압축을 푸는 데 실패했습니다.`n$($_.Exception.Message)"
}

# 압축을 풀면 'kimoonz-<브랜치이름>' 처럼 한 겹 더 들어가 있다.
$inner = Get-ChildItem $tmpDir -Directory | Select-Object -First 1
if (-not $inner) { Die '내려받은 파일이 비어 있습니다.' }

# --- 덮어쓰기 ---------------------------------------------------------------
# 내려받은 압축에는 설정(config.yaml)도, 로그인 세션(.state)도, 파이썬 환경(.venv)도
# 들어 있지 않다. 그래서 그냥 덮어써도 쓰던 것이 지워지지 않는다.
# 폴더째 지우는 방식은 쓰지 않는다 — 그 폴더 안에서 실행하면 Windows 가 잠가 실패하고,
# 무엇보다 설정과 로그인 정보가 날아간다.
New-Item -ItemType Directory -Force -Path $Dest | Out-Null
try {
    Copy-Item -Path (Join-Path $inner.FullName '*') -Destination $Dest -Recurse -Force
} catch {
    Die @"
코드를 덮어쓰지 못했습니다.
프로그램 창이 열려 있으면 닫고 다시 실행해 주세요.
$($_.Exception.Message)
"@
}
Remove-Item -Recurse -Force $tmpDir, $zip -ErrorAction SilentlyContinue

if ($isUpdate) { Say '최신 코드로 바꿨습니다. (설정과 로그인 정보는 그대로)' }
else           { Say "받았습니다: $Dest" }
Say ''

# --- 이어서 설치 ------------------------------------------------------------
$setup = Join-Path $Dest 'setup.bat'
if (-not (Test-Path $setup)) { Die "설치 파일을 찾지 못했습니다: $setup" }

Say '이어서 준비를 시작합니다. (처음 한 번은 몇 분 걸립니다)'
Say ''

Set-Location $Dest
& cmd /c $setup
