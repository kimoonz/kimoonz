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

## 시작하기

**Windows** — 폴더를 받은 뒤 `setup.bat` 을 더블클릭하세요.

**macOS / Linux** — 터미널에서:

```bash
./setup.sh
```

파이썬 확인 → 필요한 것 설치 → 브라우저 내려받기 → 설정까지 알아서 이어집니다.
(파이썬이 없으면 어디서 받는지 알려줍니다. 시스템 파이썬은 건드리지 않고
이 폴더 안 `.venv` 에만 설치합니다.)

날짜를 미리 정해두셨다면 이렇게 넘기면 묻지 않습니다.

```bash
./setup.sh --date 2026-09-19 --nights 1        # macOS / Linux
setup.bat --date 2026-09-19 --nights 1         # Windows (명령 프롬프트에서)
```

설치가 끝나면 브라우저가 뜹니다. **안내대로 클릭만** 하시면 됩니다. 2~3분이면 끝납니다.

```
1/4  로그인 화면까지 들어가 주세요            → Enter
2/4  직접 로그인하고 예약 달력까지 가주세요    → Enter
3/4  날짜를 눌러 캐빈 목록을 띄워 주세요       → Enter
4/4  (선택) 예약자 정보 화면까지 가주세요      → Enter
```

그 사이에 화면 구조와 사이트가 쓰는 재고 조회 통신을 관찰해서 **설정 파일을 알아서
만들고**, 제대로 읽히는지 확인한 뒤, 바로 감시를 시작할지 물어봅니다.
단계마다 화면이 맞는지 확인해 주므로, 엉뚱한 데서 Enter 를 눌러도 그 자리에서 알려줍니다.

날짜를 미리 정해두셨다면 물어보지 않게 할 수도 있습니다.

```bash
python -m paradogo --date 2026-09-19 --nights 1 --zones C,D start
```

설정이 끝난 뒤로는 파일 두 개만 쓰시면 됩니다.

| 하고 싶은 것 | Windows | macOS / Linux |
| --- | --- | --- |
| 감시 시작 (계속 켜두기) | `watch.bat` 더블클릭 | `./watch.sh` |
| 지금 돌고 있나 확인 | `status.bat` 더블클릭 | `./status.sh` |

명령어로 쓰신다면 `python -m paradogo track --forever` / `python -m paradogo status` 입니다.

### 자동 로그인

상시 감시를 돌리면 세션은 언젠가 만료됩니다. 새벽에 풀렸는데 사람이 다시 로그인해야
한다면 아침까지 감시가 멈춘 것이나 마찬가지입니다. 한 번 저장해 두면 알아서 다시
로그인합니다.

```bash
python -m paradogo login --save
```

아이디를 묻고, 비밀번호는 **화면에 보이지 않게** 입력받은 뒤 저장하고,
실제로 로그인이 되는지까지 확인합니다.

| 저장 위치 | 조건 |
| --- | --- |
| **OS 키체인** (Windows 자격 증명 관리자 / macOS 키체인 / Linux Secret Service) | `pip install keyring` 이 돼 있을 때 |
| `.state/credentials.json` (권한 600) | 키체인을 못 쓸 때 |

파일로 저장되는 경우 **암호화가 아닙니다.** 그 PC 를 쓸 수 있는 사람은 읽을 수 있습니다.
대신 `.state/` 는 통째로 git 에서 제외돼 있어 저장소에는 올라가지 않습니다.
더 안전하게 하려면 `pip install keyring` 을 먼저 하세요.

지우려면 `python -m paradogo login --forget`.

> **설정 YAML 에는 절대 비밀번호를 적지 마세요.** 실수로 커밋되면 그대로 공개됩니다.
> 이 도구는 YAML 에 비밀번호가 없어도 동작하도록 만들어져 있습니다.

> **캡차·본인확인·간편로그인이 걸린 사이트라면 자동 로그인이 불가능합니다.**
> 그때는 `login --manual` 로 직접 로그인해 세션을 저장해 두고, 세션이 풀리면
> 알림을 받아 한 번 더 해주셔야 합니다.

## 계속 켜두기

`--forever` 를 붙이면 멈춰도 알아서 다시 뜹니다.

* 조회가 계속 실패하거나 예기치 못한 오류로 죽으면 **30초 → 최대 15분** 간격으로 재시작
* 로그인 세션이 풀린 건 재시도로 낫지 않으므로, 알림을 보내고 기다립니다
  (`python -m paradogo login --manual` 한 번이면 알아서 이어집니다)
* 매 회차 상태를 남겨 다른 창에서 `status` 로 확인할 수 있습니다
* 하루 한 번 "아직 지켜보는 중" 요약을 보냅니다 — 조용한 게 정상인지 죽은 건지 구분되게

```
$ python -m paradogo status
● 감시 중 (PID 41207)
  가동 시간   : 3일 2시간 11분
  마지막 확인 : 4초 전 (13,208회차)
  재고        : 240칸 중 2칸 예약가능
  누적 취소   : 7건 · 재시작 2회
  조회 경로   : api
```

### PC 를 켜면 자동으로 뜨게

재부팅해도 알아서 살아나게 하려면 OS 에 등록합니다.

```bash
python -m paradogo service            # 무엇을 등록할지 먼저 보여줍니다
python -m paradogo service --install  # 실제로 등록
```

Windows 는 작업 스케줄러(로그온 시 실행), macOS 는 launchd, Linux 는 systemd 사용자
서비스로 등록됩니다. 지금 준 `--date` 같은 조건은 등록되는 명령에 그대로 실립니다.

```bash
python -m paradogo --date 2026-09-19 --nights 1 service --install
```

재시작 직후에는 이전 상태를 이어받아, **재시작하는 동안 나온 취소까지** 확인합니다
(단, 한 시간 넘게 꺼져 있었다면 그동안의 변화를 전부 '방금 취소'로 알리지 않도록
기준선으로만 씁니다).

> **절전은 꺼두세요.** PC 가 잠들면 감시도 멈춥니다.
> Windows: 설정 > 전원 및 절전 > 절전 모드 '안 함' /
> macOS: 시스템 설정 > 배터리 > '디스플레이가 꺼져도 자동으로 잠자지 않음'

> **백그라운드로 돌 때는 브라우저 창이 없습니다.** 취소를 잡으면 결제 화면을 띄워
> 드릴 수 없으므로, 알림을 받고 **직접 홈페이지에 로그인해 '예약확인 / 결제대기'에서
> 결제**하셔야 합니다. 결제 화면을 눈앞에 띄워 받고 싶다면, 서비스 등록 대신
> 평소 쓰는 화면에서 터미널을 열어 `track --forever` 를 돌려 두세요.

아래는 세부 설정을 직접 만지고 싶을 때 보는 내용입니다.

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

## 손으로 설정하고 싶다면

`setup` 과 `start` 가 해주는 일을 단계별로 나눠 놓은 명령들입니다. 보통은 쓸 일이 없습니다.

```bash
pip install -r requirements.txt
python -m playwright install chromium
cp config/config.example.yaml   config/config.yaml
cp config/selectors.example.yaml config/selectors.yaml

python -m paradogo discover --interactive   # 화면 구조에서 셀렉터 뽑기
python -m paradogo login --manual           # 직접 로그인해서 세션 저장
python -m paradogo sniff                    # 재고 조회 API 찾기
python -m paradogo scan                     # 읽기 전용으로 확인
python -m paradogo doctor                   # 전체 점검
```

`scan` 은 아무것도 클릭하지 않고 읽기만 합니다. 여기서 날짜가 제대로 보이면
설정이 맞다는 뜻이고, 안 보이면 `track`/`snipe` 도 동작하지 않습니다.

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
| `site.base_url` | 파라다이스 **공식 홈페이지**(`www.paradisespa.co.kr`). 제휴/재판매 사이트가 아닙니다 |
| `site.login_path` / `site.booking_path` | **실제 사이트 주소창을 보고 채워야 합니다** |
| `target.check_in_dates` | 잡고 싶은 체크인 날짜, 우선순위 순 |
| `target.nights_options` | 몇 박으로 잡을지, 우선순위 순. 예: `[2, 1]` |
| `target.zones` / `exclude_zones` | 구역 A~H 우선순위 / 제외 |
| `target.cabin_types` | 희망 캐빈 이름(부분 일치). 비우면 아무거나 |
| `run.dry_run` | `true` 면 빈자리만 확인하고 예약 버튼을 누르지 않음 |

### 무엇을 잡을지 — 박수와 구역

```yaml
target:
  check_in_dates: ["2026-10-03", "2026-10-04"]   # 날짜 우선순위
  nights_options: [2, 1]        # 2박 먼저, 안 되면 1박
  zones: ["C", "D"]             # C구역 먼저, 없으면 D구역
  exclude_zones: ["A"]          # A구역은 절대 안 잡음
  cabin_types: ["프리미엄"]      # 캐빈 이름 부분 일치
```

**날짜 → 박수 → 구역 → 캐빈** 순으로 우선순위를 훑어 첫 성공에서 멈춥니다.
`[2, 1]` 이면 2박으로 먼저 조회하고, 그 조건에 자리가 없으면 같은 날짜를 1박으로 다시 봅니다.

박수는 사이트 방식에 따라 세 가지를 순서대로 시도합니다.
① 박수 선택 박스(`booking.nights_select`) → ② `{nights}박` 버튼(`booking.nights_button`)
→ ③ **체크아웃 날짜 칸 클릭**(체크인 + 박수 일자). 국내 예약 달력은 대개 ③입니다.

구역은 `A구역` `구역 A` `A존` `A동` `A-03` `캐빈 A` 같은 표기를 자동 인식합니다.
표기가 특이하면 `target.zone_pattern` 에 정규식을 직접 주거나(캡처 그룹 1번이 구역),
`selectors.yaml` 의 `booking.room_zone` 으로 구역이 적힌 요소를 지정하면 됩니다.
API가 구역을 따로 준다면 `api.zone_field` 를 쓰세요.

> **구역이 제대로 읽히는지는 `scan` 으로 먼저 확인하세요.** 출력 끝에
> `구역 인식 결과: A 31칸, C 31칸 …` 이 나옵니다. 전부 `미상` 이면 `zones` 설정이
> 아무 효과도 내지 못합니다.
>
> 구역을 못 읽은 캐빈은 기본적으로 **예약 후보에서 제외**됩니다(`zone_strict: true`).
> 엉뚱한 구역을 잡아 결제 화면까지 가는 것보다 낫기 때문입니다. 취소 **알림**은
> 그대로 오므로 기회를 놓치지는 않습니다.

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
| `start` | **처음 쓰면 이것부터** — 설정부터 감시 시작까지 한 번에 |
| `doctor` | 설정·셀렉터·알림·Playwright 점검 |
| `next-open` | 다음 오픈 시각과 이때 열리는 투숙 월 계산 (서버 시계 기준) |
| `discover` | 실제 화면에서 셀렉터 후보 수집 |
| `sniff` | 재고 조회 API 찾기 (추적 속도 향상) |
| `login` | 로그인 (`--save` 자동 로그인 저장 / `--manual` 직접 로그인 / `--forget` 삭제) |
| `scan` | **지금 예약 가능한 날짜만 조회 (읽기 전용)** |
| `notify-test` | 알림 채널 테스트 발송 |
| `snipe` | 오픈 시각에 맞춰 예약 시도 (오픈런) |
| `track` | **실시간 재고 추적 — 취소 순간을 잡는다** |
| `stats` | 추적 이력 통계 |
| `status` | 감시가 지금 돌고 있는지 확인 |
| `service` | PC 를 켜면 자동으로 뜨도록 등록 |
| `watch` | 단순 반복 확인 (`track` 이 있으면 보통 필요 없음) |

전역 옵션은 **하위 명령 앞**에 씁니다: `python -m paradogo --dry-run snipe`

| 전역 옵션 | 설명 |
| --- | --- |
| `--date YYYY-MM-DD` | 대상 날짜를 설정 대신 지정 (여러 번 가능) |
| `--nights 2,1` | 박수 우선순위 |
| `--zones C,D` / `--exclude-zones A` | 구역 우선순위 / 제외 |
| `--dry-run` / `--no-dry-run` | 예약 클릭 여부 |
| `--headless` / `--headful` | 브라우저 창 |

YAML을 고치지 않고 바로 다른 날짜를 노릴 수 있습니다.

```bash
# 9월 19일(토) 1박 — C구역 우선, A구역 제외
python -m paradogo --date 2026-09-19 --nights 1 --zones C,D --exclude-zones A track
```

### 어느 명령을 써야 하나 — 오픈이 지났는지부터 보세요

캐빈파크는 **매달 1일에 다음 달** 예약을 엽니다. 즉 **9월 투숙분은 8월 1일 09:00**에
이미 열렸습니다. 그 시각이 지난 날짜는 오픈런으로 잡을 수 없고, 취소가 나오기를
기다리는 수밖에 없습니다.

```bash
python -m paradogo --date 2026-09-19 doctor
```

```
[오픈] 다음 오픈 2026-09-01 09:00 (남은 시간 3일 14시간)
       2026-09-19 · 오픈 2026-08-01 09:00 — 이미 지남 → 취소표(track)만 가능
       ⚠ 모든 대상 날짜의 오픈이 지났습니다. `snipe` 대신 `track` 을 쓰세요.
```

| 상황 | 쓸 명령 |
| --- | --- |
| 오픈이 아직 안 왔다 | `snipe` (오픈런) — 정각에 잡는다 |
| 오픈이 이미 지났다 | `track` (취소표 추적) — 누가 취소하기를 기다린다 |

오픈이 지난 날짜로 `snipe` 를 돌리면 헛수고이므로 실행 자체를 막습니다
(정말 강행하려면 `--force`).

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

```
구역별 취소 발생
  C구역  9건
  A구역  2건
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
| 뭐가 잘 안 됨 | 일단 `python -m paradogo start` 를 다시 돌려 보세요. 대부분 여기서 해결됩니다 |
| `셀렉터 'xxx' 를 찾지 못했습니다` | 사이트 DOM이 다르거나 바뀐 것. `discover` 로 다시 수집해 해당 키를 교체 |
| 로그인이 매번 다시 됨 | `login.success_marker` 가 잘못됨. 로그인 상태에서만 보이는 요소로 바꾸세요 |
| 로그인 실패 | 캡차·본인확인 단계가 있으면 자동 로그인은 불가. `login` 을 `--headful` 로 돌려 직접 로그인한 뒤 세션을 저장하세요 |
| 날짜를 못 찾음 | `booking.day_cell` 의 placeholder 확인. `{date}` `{day}` `{day_int}` `{compact}` 중 실제 DOM에 맞는 것 |
| `snipe` 가 바로 멈춤 | 그 날짜의 오픈이 이미 지난 것. `track` 을 쓰세요 (`doctor` 가 날짜별로 알려줍니다) |
| 결제 페이지 판정 실패 | `payment.marker` 를 결제 화면에만 있는 텍스트/요소로 바꾸세요 |
| 구역이 전부 `미상` | `scan` 의 '구역 인식 결과' 확인 → `target.zone_pattern` 또는 `booking.room_zone` 지정 |
| 2박이 안 잡힘 | `booking.checkout_cell`(또는 `nights_select` / `nights_button`) 확인. 로그에 "체크아웃 …를 눌러 N박으로 맞췄습니다" 가 찍히는지 보세요 |
| 알림이 안 옴 | `python -m paradogo notify-test` 로 채널부터 확인 |
| `scan` 이 아무것도 못 찾음 | 셀렉터/`api` 설정 문제. 여기서 안 되면 `track`·`snipe` 도 안 됩니다 |
| 추적이 조용함 | 먼저 `status` 로 살아 있는지 보세요. 그다음 `stats` 의 폴링 성공률 — 낮으면 취소가 없는 게 아니라 조회가 실패 중입니다 |
| 자고 일어나니 꺼져 있음 | PC 절전 때문입니다. 절전을 끄고 `service --install` 로 등록하세요 |
| "다시 로그인이 필요합니다" 알림 | `login --save` 를 해두면 이 알림 자체가 안 옵니다. 캡차가 있는 사이트라면 `login --manual` 로 한 번 더 |
| `No module named paradogo` | 저장소 폴더 밖에서 실행한 것입니다. `cd kimoonz` 후 다시 실행하세요 |
| 처음부터 뭐가 안 됨 | `python -m paradogo doctor` — 설정 전에도 Python·Playwright·브라우저·접속을 점검합니다 |
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
  credentials.py 로그인 정보 보관 (OS 키체인 우선)
  supervisor.py 계속 켜두기 — 재시작·상태 기록·하루 요약
  service.py    systemd / launchd / 작업 스케줄러 등록 파일 생성
  wizard.py     `start` 설치 마법사 — 화면을 관찰해 설정을 자동 생성
  zones.py      구역(A~H) 인식과 우선순위
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
* 구역·박수: A구역 제외 + C/D 우선 설정으로, A구역 취소는 알림만 하고 건너뛴 뒤
  C구역 취소를 2박(체크아웃 날짜 클릭)으로 확보. 2박 자리가 없는 구역에서는
  1박으로 폴백해 확보하는 것까지 확인
* `sniff`: 목업의 XHR을 엿들어 재고 API와 응답 구조(구역 필드 포함)를 자동 추출
* `start` 마법사: 4단계를 전부 거쳐 config.yaml / selectors.yaml 을 자동 생성하고,
  그 파일만으로 조회가 되는지까지 확인
* 자동 로그인: 세션을 지운 상태에서 저장해 둔 정보로 다시 로그인해 감시를 이어감.
  설정에 계정 정보가 있을 때 / 보관소에만 있을 때 / 둘 다 없을 때(사람에게 알림)
  세 경우 모두 확인
* 상시 감시: `--forever` 로 띄운 상태에서 사이트를 죽여 조회를 연속 실패시키자
  감시가 중단됐다가 자동으로 재시작했고, 사이트를 되살리자 직전 상태를 이어받아
  다시 감시로 복귀 — 그동안 `status` 가 살아 있음/죽음을 정확히 보고
