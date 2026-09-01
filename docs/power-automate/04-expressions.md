# 4. 표현식(Expressions)

동적 콘텐츠로 안 되는 계산·변환은 전부 표현식으로 합니다.
문법은 Azure Logic Apps의 **워크플로 정의 언어(WDL)** 와 동일합니다.

## 4.1 문법 기본

### `@` 와 `@{ }` 의 차이

| 표기 | 쓰는 곳 | 의미 |
| --- | --- | --- |
| `@expression` | 필드 **전체**가 표현식일 때, 트리거 조건 | 값 자체를 반환 (타입 유지) |
| `@{expression}` | 문자열 **안**에 값을 끼워 넣을 때 | 문자열로 보간 |

```
안녕하세요, @{triggerBody()?['name']}님          ← 문자열 보간
@triggerBody()?['count']                          ← 숫자 그대로 반환
@{add(1, 2)}건 처리했습니다                        ← "3건 처리했습니다"
```

> UI의 표현식 편집기에 입력할 때는 `@`를 빼고 함수만 씁니다.
> `@`/`@{}`는 코드 뷰나 트리거 조건에서 직접 작성할 때 필요합니다.

### 리터럴 `@` 출력

문자열이 `@`로 시작해야 하면 `@@`로 이스케이프합니다.

### 액션 이름의 공백 → 밑줄

표현식에서 액션을 참조할 때 **이름의 공백은 `_`로 바꿉니다.**

```
액션 이름: 항목 가져오기
표현식:    body('항목_가져오기')
```

한글 액션 이름도 동일합니다. 이름을 바꾸면 참조하던 표현식이 깨지므로,
**액션 이름은 만들 때 확정**하는 게 좋습니다.

### null 안전 접근 `?[ ]`

```
triggerBody()['user']['email']    ← user가 없으면 런타임 오류
triggerBody()?['user']?['email']  ← 없으면 null 반환 (안전)
```

**실무에서는 항상 `?[ ]`를 쓰세요.** 오류의 상당수가 여기서 나옵니다.

## 4.2 참조 함수

| 함수 | 설명 |
| --- | --- |
| `triggerBody()` | 트리거 출력의 body |
| `triggerOutputs()` | 트리거 전체 출력(헤더 포함) |
| `body('액션명')` | 해당 액션 출력의 body |
| `outputs('액션명')` | 해당 액션의 전체 출력 |
| `actions('액션명')` | 액션의 실행 결과 객체(`status`, `error` 등 포함) |
| `variables('변수명')` | 변수 값 |
| `items('반복액션명')` | 지정한 반복의 현재 항목 |
| `item()` | 가장 가까운 반복/필터의 현재 항목 |
| `result('범위명')` | 범위 안 액션들의 결과 배열 (6장) |
| `workflow()` | 플로우 자신의 메타데이터(실행 ID, 이름 등) |

```
중첩 반복에서 바깥 항목 참조:
  items('각각에_적용')?['Title']        ← 바깥 반복
  items('각각에_적용_2')?['Amount']     ← 안쪽 반복
```

> `item()`은 중첩되면 어느 것을 가리키는지 헷갈립니다. **중첩 시에는 `items('이름')`을 명시**하세요.

### 실행 기록 링크 만들기

```
@{concat(
  'https://make.powerautomate.com/manage/environments/',
  workflow()?['tags']?['environmentName'],
  '/flows/', workflow()?['name'],
  '/runs/', workflow()?['run']?['name']
)}
```

실패 알림 메일에 이 링크를 넣으면 바로 해당 실행으로 이동합니다(6장).

## 4.3 문자열 함수

| 함수 | 예시 | 결과 |
| --- | --- | --- |
| `concat(a, b, ...)` | `concat('AB', '-', '01')` | `AB-01` |
| `substring(s, start, len)` | `substring('20260901', 0, 4)` | `2026` |
| `replace(s, old, new)` | `replace('a-b', '-', '/')` | `a/b` |
| `split(s, sep)` | `split('a,b,c', ',')` | `["a","b","c"]` |
| `toUpper` / `toLower` | `toUpper('abc')` | `ABC` |
| `trim(s)` | `trim('  x  ')` | `x` |
| `length(s)` | `length('abc')` | `3` |
| `indexOf(s, sub)` | `indexOf('abcd', 'c')` | `2` (없으면 `-1`) |
| `lastIndexOf(s, sub)` | | 마지막 위치 |
| `startsWith` / `endsWith` | `startsWith('[긴급] 건', '[긴급]')` | `true` |
| `guid()` | | 새 GUID |
| `formatNumber(n, fmt)` | `formatNumber(1234567, '#,##0')` | `1,234,567` |
| `slice(s, start, end)` | `slice('abcdef', 1, 3)` | `bc` |

**주의**: `substring`은 범위를 벗어나면 **오류**입니다. 안전하게 자르려면:

```
@if(greater(length(변수), 100), concat(substring(변수, 0, 100), '...'), 변수)
```

## 4.4 컬렉션 함수

| 함수 | 설명 |
| --- | --- |
| `length(arr)` | 개수 |
| `empty(x)` | 비었는지 (문자열/배열/객체 모두) |
| `first(arr)` / `last(arr)` | 첫/마지막 항목 |
| `take(arr, n)` / `skip(arr, n)` | 앞 n개 / 앞 n개 제외 |
| `contains(coll, v)` | 포함 여부 (문자열의 부분 문자열도 가능) |
| `join(arr, sep)` | 문자열로 결합 |
| `union(a, b)` | 합집합 (중복 제거) |
| `intersection(a, b)` | 교집합 |
| `createArray(a, b, ...)` | 배열 생성 |
| `range(start, count)` | `range(1, 3)` → `[1,2,3]` |
| `reverse(arr)` | 역순 |
| `sort(arr)` / `sort(arr, 'key')` | 정렬 |

**중복 제거 관용구**

```
@union(body('선택'), body('선택'))
```

`union`은 중복을 제거하므로 자기 자신과 합치면 distinct가 됩니다.

## 4.5 논리 · 비교 함수

| 함수 | 설명 |
| --- | --- |
| `if(조건, 참값, 거짓값)` | 삼항 연산 |
| `and(...)` / `or(...)` / `not(x)` | 논리 연산 |
| `equals(a, b)` | 같음 |
| `greater` / `greaterOrEquals` | `>` / `>=` |
| `less` / `lessOrEquals` | `<` / `<=` |
| `coalesce(a, b, ...)` | 첫 번째 non-null 값 |

```
@if(empty(triggerBody()?['담당자']), '미지정', triggerBody()?['담당자'])
@coalesce(triggerBody()?['닉네임'], triggerBody()?['이름'], '익명')
```

> ⚠️ `if()`는 **양쪽 인수를 모두 평가**합니다. 거짓 분기에만 유효한 표현식을 넣어도 오류가 납니다.
> 예: `if(empty(arr), '없음', first(arr)?['Title'])` → `arr`가 비면 `first()`에서 오류.
> 이럴 땐 `조건` 액션으로 분기하세요.

> ⚠️ 타입이 다르면 `equals`는 `false`입니다. `equals('1', 1)` → `false`.
> 비교 전에 `int()` / `string()` 으로 맞추세요.

## 4.6 변환 함수

| 함수 | 설명 |
| --- | --- |
| `int(x)` / `float(x)` / `string(x)` / `bool(x)` | 형 변환 |
| `json(s)` | 문자열 → 객체/배열 |
| `string(obj)` | 객체 → JSON 문자열 |
| `array(x)` | 값을 단일 요소 배열로 |
| `xml(x)` / `xpath(xml, path)` | XML 처리 |
| `base64(s)` | 인코딩 |
| `base64ToString(s)` | 디코딩 |
| `base64ToBinary(s)` / `binary(x)` | 이진 데이터 |
| `dataUriToBinary` / `dataUriToString` | data URI 처리 |
| `uriComponent(s)` | URL 인코딩 |
| `uriComponentToString(s)` | URL 디코딩 |

## 4.7 객체 조작 함수

| 함수 | 설명 |
| --- | --- |
| `setProperty(obj, key, value)` | 속성 설정(있으면 덮어씀) |
| `addProperty(obj, key, value)` | 속성 추가(이미 있으면 오류) |
| `removeProperty(obj, key)` | 속성 제거 |

**JSON을 문자열 조립 없이 안전하게 만드는 법** (3.6절 참고)

```
@setProperty(
  setProperty(
    setProperty(json('{}'), 'id',   triggerBody()?['id']),
                            'name', triggerBody()?['name']),
                            'memo', triggerBody()?['memo'])
```

값에 따옴표·줄바꿈이 들어 있어도 깨지지 않습니다.

## 4.8 수학 함수

`add`, `sub`, `mul`, `div`, `mod`, `min`, `max`, `rand(min, max)`

> ⚠️ `div(7, 2)`는 정수 나눗셈이라 `3`입니다. 소수가 필요하면 `div(7.0, 2)` 또는 `div(float(7), 2)`.

## 4.9 날짜/시간 함수

| 함수 | 예시 |
| --- | --- |
| `utcNow()` | `2026-09-01T04:30:00.0000000Z` |
| `utcNow('yyyy-MM-dd')` | `2026-09-01` |
| `formatDateTime(dt, fmt)` | `formatDateTime(utcNow(), 'yyyy-MM-dd HH:mm')` |
| `addDays/addHours/addMinutes/addSeconds(dt, n)` | `addDays(utcNow(), -7)` |
| `addToTime(dt, n, unit)` | `addToTime(utcNow(), 3, 'Month')` |
| `subtractFromTime(dt, n, unit)` | |
| `startOfDay/startOfHour/startOfMonth(dt)` | |
| `dayOfWeek(dt)` | 0=일요일 … 6=토요일 |
| `dayOfMonth` / `dayOfYear` | |
| `ticks(dt)` | 정렬·차이 계산용 정수 |
| `getPastTime(n, unit)` / `getFutureTime(n, unit)` | |
| `convertTimeZone(dt, from, to, fmt)` | 아래 참고 |

### 한국 시간 처리 — 반드시 알아야 할 것

**플로우 내부의 모든 시간은 UTC입니다.** 한국 시간으로 보여 주려면 변환해야 합니다.

```
@convertTimeZone(utcNow(), 'UTC', 'Korea Standard Time', 'yyyy-MM-dd HH:mm')
```

- `'Korea Standard Time'` 은 **Windows 표준 시간대 ID**입니다. `'Asia/Seoul'` 은 동작하지 않습니다.
- 반대 방향(한국 시간 문자열 → UTC)은 `convertToUtc(dt, 'Korea Standard Time')`.

**"오늘"의 함정**

한국 시간 09:00 이전에는 UTC 날짜가 아직 어제입니다.
"오늘 등록된 항목" 필터를 만들 때 `utcNow('yyyy-MM-dd')`를 쓰면 날짜가 하루 어긋납니다.

```
오늘(KST) 시작 시각을 UTC로:
@{convertToUtc(
    startOfDay(convertTimeZone(utcNow(), 'UTC', 'Korea Standard Time')),
    'Korea Standard Time'
  )}
```

이 값을 OData 필터에 넣으면 정확합니다.

### 서식 문자열

| 패턴 | 결과 |
| --- | --- |
| `yyyy-MM-dd` | `2026-09-01` |
| `yyyy년 M월 d일` | `2026년 9월 1일` |
| `HH:mm:ss` | 24시간 |
| `hh:mm tt` | 12시간 + AM/PM |
| `o` | ISO 8601 왕복 형식 |

> **`MM`은 월, `mm`은 분입니다.** 대소문자를 바꿔 쓰는 실수가 매우 흔합니다.
> 요일 이름 같은 문화권 의존 서식은 로캘에 따라 달라지므로, 한국어 요일이 필요하면
> `dayOfWeek()` 결과를 배열에서 꺼내 쓰세요:
> `@{first(skip(createArray('일','월','화','수','목','금','토'), dayOfWeek(utcNow())))}`
> (함수 호출 결과에 `[n]` 인덱서를 바로 붙이면 파싱되지 않을 수 있어 `skip`+`first`를 씁니다.
> 배열을 `작성` 액션에 담아 두었다면 `outputs('요일배열')[dayOfWeek(utcNow())]` 도 됩니다.)

## 4.10 자주 쓰는 관용구 모음

```
빈 값 기본값 처리
  @coalesce(triggerBody()?['부서'], '미지정')

숫자 0 채우기 (사번 6자리)
  @formatNumber(int(triggerBody()?['empNo']), '000000')   →  000042

배열에서 특정 필드만 뽑아 콤마로
  @join(body('선택'), ', ')

배열이 비었는지
  @empty(body('배열_필터링'))

문자열 안전 자르기(100자 + 말줄임)
  @if(greater(length(v), 100), concat(substring(v, 0, 100), '...'), v)

HTML 태그 제거(간단 버전)
  @trim(replace(replace(v, '<br>', ' '), '&nbsp;', ' '))

파일 확장자 추출
  @toLower(last(split(triggerBody()?['fileName'], '.')))

금액 콤마 표기
  @formatNumber(int(triggerBody()?['amount']), '#,##0')  →  1,234,567
```

## 4.11 표현식 디버깅 요령

1. **작게 쪼개기** — 긴 표현식을 `작성` 액션 여러 개로 나눠 중간값을 확인합니다.
2. **원시 출력 보기** — 실행 기록에서 액션을 펼치고 *원시 출력 표시*를 눌러 실제 JSON 구조를 봅니다.
   동적 콘텐츠 이름이 아니라 **실제 키 이름**을 확인해야 합니다.
3. **`string()`으로 감싸기** — 무엇이 들어오는지 모를 때 `@{string(outputs('X'))}`로 통째로 찍어 봅니다.
4. **`?[ ]` 먼저 의심** — `null` 관련 오류의 대부분은 안전 접근자 누락입니다.

---

이전: [3. 액션과 데이터 처리](03-actions-and-data.md) · 다음: [5. 제어 흐름](05-control-flow.md)
