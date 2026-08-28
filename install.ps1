# 파라다이스 도고 캐빈 예약 도우미 — Windows 한 줄 설치
#
# PowerShell 을 열고 아래 한 줄을 붙여넣으세요.
#
#   irm https://raw.githubusercontent.com/kimoonz/kimoonz/claude/paradise-dogo-cabin-booking-y5lryk/install.ps1 | iex
#
# 하는 일: 내 문서 폴더에 코드를 내려받고 setup.bat 을 실행합니다.
# (무엇을 하는지 먼저 보고 싶으면 위 주소를 브라우저로 열어 읽어 보세요)

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
Say ' 파라다이스 도고 캐빈 예약 도우미 - 설치'
Say '=================================================='
Say ''

# TLS 1.2 를 켜지 않으면 구버전 PowerShell 에서 GitHub 접속이 막힌다.
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch { }

if (Test-Path $Dest) {
    Say "이미 폴더가 있습니다: $Dest"
    $answer = Read-Host '지우고 새로 받을까요? 기존 설정과 로그인 정보는 사라집니다 (y/N)'
    if ($answer -notmatch '^[yY]') {
        Say ''
        Say '기존 폴더를 그대로 씁니다.'
    } else {
        Remove-Item -Recurse -Force $Dest
    }
}

if (-not (Test-Path $Dest)) {
    $zipUrl = "https://codeload.github.com/$Repo/zip/refs/heads/$Branch"
    $zip    = Join-Path $env:TEMP 'paradogo.zip'
    $tmpDir = Join-Path $env:TEMP 'paradogo-unzip'

    Say "코드를 내려받는 중... ($Repo)"
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

    New-Item -ItemType Directory -Force -Path (Split-Path $Dest) | Out-Null
    Move-Item $inner.FullName $Dest
    Remove-Item -Recurse -Force $tmpDir, $zip -ErrorAction SilentlyContinue
    Say "받았습니다: $Dest"
}

Say ''
$setup = Join-Path $Dest 'setup.bat'
if (-not (Test-Path $setup)) { Die "설치 파일을 찾지 못했습니다: $setup" }

Say '이어서 설치를 시작합니다. (처음 한 번은 몇 분 걸립니다)'
Say ''

# 설정 마법사가 키보드 입력을 받아야 하므로 같은 창에서 이어 실행한다.
Set-Location $Dest
& cmd /c $setup
