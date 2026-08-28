# paradogo — 파라다이스 스파 도고 캐빈파크 예약 보조 도구

캐빈파크는 **매달 1일 09:00(KST)에 다음 달 예약이 열리고** 인기 날짜는 곧바로 마감됩니다.
이 도구는 두 가지 국면을 자동화합니다.

* **오픈런 (`snipe`)** — 1일 09:00 정각에 로그인 → 달력 이동 → 날짜 클릭 → 캐빈 선택 →
  예약자 정보 입력까지.
* **실시간 재고 추적 (`track`)** — 오픈런에서 놓친 뒤, **누군가 취소하는 순간**을 잡는다.
  재고 상태를 계속 기억하다가 `마감 → 예약가능` 으로 뒤집히면 곧바로 확보하러 간다.

둘 다 **결제 페이지 직전에서 멈추고** 알림을 보냅니다.

## 취소를 잡는 원리

단순히 "빈자리 있나?"를 반복해서 묻는 것(`watch`)과는 다릅니다. 추적기는 그 달 전체
(날짜 × 캐빈)의 상태를 한 장의 스냅샷으로 찍고 직전 스냅샷과 비교합니다.

```
09:20:00  2026-10-03 프리미엄 캐빈 B  마감
09:20:20  2026-10-03 프리미엄 캐빈 B  예약가능   ← 방금 누가 취소했다. 즉시 확보 시도
```

그래서 '원래 비어 있던 자리'와 '방금 풀린 자리'를 구분하고, 이력이 SQLite에 쌓이므로
`stats` 로 **어느 날짜가 몇 시에 잘 풀리는지**, **취소표가 평균 몇 초 만에 사라지는지**
까지 볼 수 있습니다.

### 조회 경로가 속도를 가른다

| 경로 | 속도 | 알 수 있는 것 |
| --- | --- | --- |
| **API** (`sniff` 로 찾음) | 한 달치 한 번에, 수십~수백 ms | 날짜 **× 캐빈** 단위, 잔여 수량까지 |
| 달력 DOM (폴백) | 한 달에 페이지 로딩 1회 | 날짜 단위까지만 |

API를 찾아두면 폴링 주기를 5초까지 줄일 수 있습니다(DOM 경로는 15초 하한).
`sniff` 는 브라우저가 오가는 XHR/fetch 응답을 엿들어 재고 조회 요청을 찾아 줍니다.

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

## 처음 한 번: 이 순서대로

```bash
cp config/config.example.yaml   config/config.yaml
cp config/selectors.example.yaml config/selectors.yaml

# 1. 셀렉터 수집 — 실제 화면을 보고 채운다
python -m paradogo discover --interactive

# 2. 로그인 세션 만들기 — 캡차·본인확인이 있으면 직접 로그인하세요
python -m paradogo login --manual

# 3. 읽기 전용으로 검증 — 지금 예약 가능한 날짜가 제대로 보이는지 확인
python -m paradogo scan

# 4. (권장) 재고 조회 API 찾기 — 추적이 훨씬 빨라진다
python -m paradogo sniff

# 5. 전체 점검
python -m paradogo doctor
```

**3번을 꼭 먼저 하세요.** `scan` 은 아무것도 클릭하지 않고 읽기만 합니다. 여기서
날짜가 제대로 보이면 셀렉터·API 설정이 맞다는 뜻이고, 안 보이면 `track`/`snipe` 도
동작하지 않습니다.

## 설정

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

### 로그인이 자동으로 안 될 때

캡차·본인확인·간편로그인이 걸린 사이트에서는 아이디/비밀번호 자동 입력이 통하지
않습니다. 그럴 때는 한 번만 손으로 로그인하세요.

```bash
python -m paradogo login --manual
```

브라우저가 뜨면 직접 로그인하고 터미널에서 Enter 를 누르면 세션이 저장되고,
이후 `scan` / `track` / `snipe` 는 그 세션을 그대로 재사용합니다.

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
| `sniff` | 재고 조회 API 찾기 (추적 속도 향상) |
| `login` | 로그인 세션 저장 (`--manual` 로 직접 로그인) |
| `scan` | **지금 예약 가능한 날짜만 조회 (읽기 전용)** |
| `notify-test` | 알림 채널 테스트 발송 |
| `snipe` | 오픈 시각에 맞춰 예약 시도 (오픈런) |
| `track` | **실시간 재고 추적 — 취소 순간을 잡는다** |
| `stats` | 추적 이력 통계 |
| `watch` | 단순 반복 확인 (`track` 이 있으면 보통 필요 없음) |

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

### 실시간 재고 추적 (취소 잡기)

```bash
# 먼저 읽기 전용으로 확인
python -m paradogo scan

# 알림만 받아보며 하루 돌려서 감을 잡는다
python -m paradogo track --alert-only

# 취소가 나오면 결제 직전까지 자동 진행
python -m paradogo --no-dry-run track
```

터미널에 달력 대시보드가 뜹니다(`--no-dashboard` 로 끄면 로그만).

```
  2026년 10월
  일 월 화 수 목 금 토
               1○  2○  3●
   4○  5○  6○  7○  8○  9○ 10○
  11● 12○ 13○ 14○ 15○ 16○ 17○

  ● 예약가능  ○ 마감  · 정보없음   대상

 최근 변화
  09:20:20 [취소 발생] 2026-10-03 프리미엄 캐빈 B
```

며칠 돌린 뒤 이력을 보면 언제 지켜봐야 하는지가 나옵니다.

```bash
python -m paradogo stats
```

```
시간대별 취소 발생
  09시 ████████ 3
  23시 ████████████████████████████████ 12

취소표가 살아 있던 시간 (짧은 순)
  2026-10-03 프리미엄 캐빈 B  38초
  → 가장 빨리 사라진 게 38초. 폴링 주기를 그보다 짧게 두어야 잡을 수 있습니다.
```

`watch` 는 상태를 기억하지 않고 매번 새로 확인만 하는 예전 방식입니다.
보통은 `track` 을 쓰세요.

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
| `scan` 이 아무것도 못 찾음 | 셀렉터/`api` 설정 문제. 여기서 안 되면 `track`·`snipe` 도 안 됩니다 |
| 추적이 조용함 | `stats` 의 폴링 성공률을 보세요. 낮으면 취소가 없는 게 아니라 조회가 실패 중입니다 |
| 취소를 계속 놓침 | `stats` 의 '살아 있던 시간'보다 폴링 주기가 길다는 뜻. `sniff` 로 API를 찾아 주기를 줄이세요 |

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
  inventory.py  재고 스냅샷과 전환 감지 (마감→예약가능 판정)
  sources.py    재고 조회 경로 — API / 달력 DOM
  store.py      추적 이력 (SQLite): 현재 상태 · 전환 로그 · 폴링 건강도
  tracker.py    실시간 추적 루프 + 취소 확보
  dashboard.py  터미널 달력 대시보드
  scan.py       읽기 전용 조회
  sniff.py      재고 API 찾기
  watcher.py    단순 반복 확인 (구방식)
  discover.py   실제 화면에서 셀렉터 후보 수집
  notify/       텔레그램 · 이메일
```

## 테스트

```bash
pip install -r requirements-dev.txt
python -m pytest
```

브라우저 없이 도는 단위 테스트입니다. 플로우 전체는 사이트를 흉내낸 로컬 목업으로
검증했습니다.

* 오픈런: 로그인 → 세션 재사용 → 9월→10월 달력 이동 → 매진 캐빈 건너뛰기 →
  희망 캐빈 선택 → 예약자 정보 → 결제 페이지에서 정지
* 추적: 전부 마감인 상태에서 시작 → 특정 캐빈에 취소를 인위적으로 발생시킴 →
  다음 폴링에서 `취소 발생` 감지 → 확보 → 결제 페이지 도달
* 조회: API 경로(날짜×캐빈, 잔여 수량)와 달력 DOM 폴백(날짜 단위) 양쪽
* `sniff`: 목업의 XHR을 엿들어 재고 API와 응답 구조를 자동 추출
