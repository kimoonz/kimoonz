# paradogo — 파라다이스 스파 도고 캐빈파크 예약 보조 도구

캐빈파크는 **매달 1일 09:00(KST)에 다음 달 예약이 열리고** 인기 날짜는 곧바로 마감됩니다.
이 도구는 그 순간에 사람이 해야 할 반복 작업(로그인 → 달력 이동 → 날짜 클릭 → 캐빈 선택 →
예약자 정보 입력)을 대신하고, **결제 페이지 직전에서 멈춘 뒤 알림을 보냅니다.**

## 결제는 자동화하지 않습니다

의도적인 설계입니다.

- 국내 PG 결제는 키보드 보안 프로그램·ISP/앱카드 팝업이 걸려 자동화가 사실상 불가능합니다.
- 무엇보다, 잘못된 날짜에 자동 결제가 되면 되돌리기 어렵습니다.

봇은 결제 페이지에 도달하면 멈추고, 텔레그램·이메일로 알린 뒤 브라우저를 열어 둡니다.
그 창에서 직접 결제하세요. **결제 전까지 예약은 확정되지 않습니다.**

## 시작하기 전에

- 개인이 본인 계정으로 쓰는 용도를 전제로 만들었습니다. 계정을 여러 개 돌리거나
  좌석을 선점해 되파는 용도로 쓰지 마세요.
- 요청 주기에 하한선(감시 10초, 오픈런 재시도 300ms)이 코드에 박혀 있습니다.
  설정으로 더 낮출 수 없습니다.
- 사이트 이용약관에 자동화 도구 관련 조항이 있는지 직접 확인하고 쓰세요.
- **오픈 시각·예약 규칙은 바뀔 수 있습니다.** 공식 안내를 먼저 확인하고 `config.yaml` 의
  `run.open_time` 을 맞추세요.

## ⚠️ 첫 실행 전 반드시 할 일: 셀렉터 수집

이 코드는 실제 사이트 DOM에 접근할 수 없는 환경에서 작성됐습니다.
`config/selectors.example.yaml` 에 들어 있는 값은 **한국 예약 사이트에서 흔한 패턴을 넣은
출발점일 뿐, 검증된 값이 아닙니다.** 실제 화면을 보고 한 번은 채워야 동작합니다.

대신 셀렉터는 전부 YAML로 빠져 있어서, 사이트가 개편돼도 **코드를 고칠 필요 없이
YAML만 바꾸면 됩니다.** 수집은 `discover` 명령이 도와줍니다.

## 설치

```bash
git clone <이 저장소>
cd kimoonz
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
```

이미 설치된 크롬을 쓰고 싶으면 `PARADOGO_CHROMIUM_PATH` 환경변수에 실행 파일 경로를 지정하세요.

## 설정

```bash
cp config/config.example.yaml   config/config.yaml
cp config/selectors.example.yaml config/selectors.yaml
```

비밀번호·토큰은 파일에 직접 쓰지 말고 `${ENV_VAR}` 로 두고 환경변수에 넣습니다.

```bash
export PARADOGO_ID='아이디'
export PARADOGO_PW='비밀번호'
export TELEGRAM_BOT_TOKEN='...'   # @BotFather 에서 발급
export TELEGRAM_CHAT_ID='...'     # @userinfobot 에게 말 걸면 알려줌
export SMTP_USER='you@gmail.com'
export SMTP_PASS='앱 비밀번호'     # Gmail 일반 비밀번호 아님
```

`config/config.yaml` 에서 최소한 이 항목들을 확인하세요.

| 항목 | 설명 |
| --- | --- |
| `site.login_path` / `site.booking_path` | **실제 사이트 주소창을 보고 채워야 합니다** |
| `target.check_in_dates` | 잡고 싶은 체크인 날짜, 우선순위 순 |
| `target.cabin_types` | 희망 캐빈 이름(부분 일치). 비우면 아무거나 |
| `run.dry_run` | `true` 면 빈자리만 확인하고 예약 버튼을 누르지 않음 |

### 셀렉터 채우기

```bash
python -m paradogo discover --interactive
```

브라우저가 뜨면 **직접** 로그인하고 캐빈파크 예약 화면까지 이동한 뒤,
터미널에서 Enter 를 누르세요. 그 화면을 분석해 `config/selectors.discovered.yaml`
초안을 만들어 줍니다. 로그인 화면 / 예약 목록 화면 / 예약자 정보 화면을 각각 한 번씩
돌리면 대부분의 키가 채워집니다. 내용을 확인·보완해 `config/selectors.yaml` 로 옮기세요.

점검:

```bash
python -m paradogo doctor
```

## 사용법

| 명령 | 하는 일 |
| --- | --- |
| `doctor` | 설정·셀렉터·알림·Playwright 점검 |
| `next-open` | 다음 오픈 시각과 이때 열리는 투숙 월 계산 (서버 시계 기준) |
| `discover` | 실제 화면에서 셀렉터 후보 수집 |
| `login` | 로그인해서 세션 파일 저장 |
| `notify-test` | 알림 채널 테스트 발송 |
| `snipe` | 오픈 시각에 맞춰 예약 시도 (오픈런) |
| `watch` | 취소표 감시 |

전역 옵션(`--headless`, `--dry-run` 등)은 **하위 명령 앞**에 씁니다:
`python -m paradogo --dry-run snipe`

### 오픈런

```bash
# 1) 하루 전: 로그인 세션을 미리 만들어 둔다
python -m paradogo login

# 2) 모의 실행으로 플로우를 한 번 확인한다 (예약 버튼은 누르지 않음)
python -m paradogo --dry-run snipe --now

# 3) 오픈 전날 밤에 띄워 두면 1일 09:00 에 알아서 시도한다
python -m paradogo --no-dry-run snipe
```

오픈 순간에 낭비할 시간이 없으므로, 봇은 미리 로그인하고 예약 페이지·달력까지
목표 월에 맞춰 놓은 채 대기합니다. 오픈 `lead_seconds` 전에 세션을 한 번 되살리고,
서버 시계 기준으로 정각까지 기다린 뒤 시도를 시작합니다.

`--at 2026-09-01T09:00:00` 으로 오픈 시각을 직접 지정할 수 있고,
`--now` 는 기다리지 않고 즉시 시도합니다(테스트용).

### 취소표 감시

오픈런에서 놓쳤을 때 씁니다. 취소가 나오면 알리고, `watch.auto_reserve: true` 면
결제 직전까지 진행합니다.

```bash
python -m paradogo watch
```

### 매달 자동 실행

**Linux/macOS (cron)** — 매달 1일 08:50 에 시작해 봇이 09:00까지 대기:

```cron
50 8 1 * * cd /path/to/kimoonz && .venv/bin/python -m paradogo --no-dry-run snipe >> snipe.log 2>&1
```

**Windows (작업 스케줄러)** — 매월 1일 08:50 트리거, 동작:
`C:\path\to\kimoonz\.venv\Scripts\python.exe`, 인수: `-m paradogo --no-dry-run snipe`,
시작 위치: `C:\path\to\kimoonz`

> 결제는 사람이 해야 하므로, 자동 실행을 걸어도 **그 시간에 PC 앞에 있어야** 합니다.
> 알림을 꼭 켜 두세요.

## 문제 해결

| 증상 | 원인과 조치 |
| --- | --- |
| `셀렉터 'xxx' 를 찾지 못했습니다` | 사이트 DOM이 다르거나 바뀐 것. `discover` 로 다시 수집해 해당 키를 교체 |
| 로그인이 매번 다시 됨 | `login.success_marker` 가 잘못됨. 로그인 상태에서만 보이는 요소로 바꾸세요 |
| 로그인 실패 | 캡차·본인확인 단계가 있으면 자동 로그인은 불가. `login` 을 `--headful` 로 돌려 직접 로그인한 뒤 세션을 저장하세요 |
| 날짜를 못 찾음 | `booking.day_cell` 의 placeholder 확인. `{date}` `{day}` `{day_int}` `{compact}` 중 실제 DOM에 맞는 것 |
| 결제 페이지 판정 실패 | `payment.marker` 를 결제 화면에만 있는 텍스트/요소로 바꾸세요 |
| 알림이 안 옴 | `python -m paradogo notify-test` 로 채널부터 확인 |

실패하면 `.artifacts/` 에 스크린샷과 HTML이 남습니다. 셀렉터를 고칠 때 그걸 보세요.

## 구조

```
paradogo/
  cli.py        커맨드라인 진입점
  config.py     YAML 로딩 + ${ENV} 치환, 요청 주기 하한선
  clock.py      오픈 시각 계산, 서버 Date 헤더 기반 시계 보정, 정밀 대기
  browser.py    Playwright 세션, 로그인 상태 저장/복원, 스크린샷
  selectors.py  후보 셀렉터 순차 시도
  flow.py       로그인 → 달력 → 날짜 → 캐빈 → 예약자 정보 → 결제 직전
  sniper.py     오픈런
  watcher.py    취소표 감시
  discover.py   실제 화면에서 셀렉터 후보 수집
  notify/       텔레그램 · 이메일
```

## 테스트

```bash
pip install -r requirements-dev.txt
python -m pytest
```

브라우저 없이 도는 단위 테스트입니다. 플로우 전체는 사이트를 흉내낸 로컬 목업 페이지로
검증했습니다(로그인 → 세션 재사용 → 9월→10월 달력 이동 → 매진 캐빈 건너뛰기 →
희망 캐빈 선택 → 예약자 정보 → 결제 페이지에서 정지).
