# 12. 포털에서 연동 플로우 만들기 (클릭 단위 안내)

이 장은 [11장 VBA 연동](11-vba-integration.md)의 **Microsoft Power Automate 쪽 절반**입니다.
아래 순서대로 만들면 `src/vba/Examples.bas`의 예제 1~3, 8이 그대로 동작합니다.

## 12.0 시작 전 확인

| 항목 | 내용 |
| --- | --- |
| 접속 주소 | <https://make.powerautomate.com> (회사/학교 Microsoft 계정으로 로그인) |
| 필요 라이선스 | HTTP 트리거는 **프리미엄**. 없다면 포털 상단 *무료 평가판 시작*(90일 체험) 가능 |
| 환경 | 우측 상단 환경 선택 확인 — 연습은 개인 개발자 환경도 무방(1.5절) |

## 12.1 10분 완성: 에코 플로우 (커넥터 0개)

먼저 아무 데이터 원본 없이 왕복부터 확인합니다.

1. 왼쪽 **만들기** → **인스턴트 클라우드 플로우** → 이름 `VBA 기본플로우`
   → 트리거 목록에서 **HTTP 요청을 수신한 경우**(When an HTTP request is received) 선택 → 만들기
   - 목록에 없으면 만든 뒤 트리거 추가에서 "요청" 또는 "request"로 검색
2. 트리거 카드를 열고 **요청 본문 JSON 스키마**에 붙여넣기
   ([`samples/http-trigger-schema.json`](../../samples/http-trigger-schema.json)과 같은 내용):

   ```json
   {
     "type": "object",
     "properties": {
       "requestId": { "type": "string" },
       "action":    { "type": "string" },
       "rows":      { "type": "array", "items": { "type": "object" } }
     },
     "required": ["action"]
   }
   ```
3. **새 단계** → "응답" 검색 → **응답(Response)** 액션 추가
   - 상태 코드: `200`
   - 헤더: `Content-Type` : `application/json`
   - 본문(표현식은 동적 콘텐츠/수식 탭에서 입력):

   ```json
   {
     "ok": true,
     "runId": "@{workflow()?['run']?['name']}",
     "echo": @{triggerBody()}
   }
   ```
   `"echo"` 값에는 따옴표가 없다는 점에 주의 — `triggerBody()`가 객체 그대로 들어갑니다.
4. **저장** → 트리거 카드를 다시 열면 **HTTP POST URL**이 생겨 있습니다 → 복사
5. Excel 즉시 실행 창(Ctrl+G):

   ```vba
   PA_SetFlowUrl "기본플로우", "<복사한 URL>"
   ```
6. `예제1_단건전송` 실행 → "성공!" 메시지에 `echo`로 보낸 내용이 그대로 돌아오면 왕복 완료.

> URL이 만들어지지 않으면 저장이 안 됐거나 라이선스 문제입니다. 화면 상단 배너를 확인하세요.

## 12.2 확장: action 분기 + 오류 경로 (예제 2·3 대응)

에코가 확인되면 12.1의 응답 액션을 지우고 아래 구조로 바꿉니다.
데이터 원본 없이도 돌아가도록 `query`는 우선 고정 샘플을 돌려줍니다.

```
[트리거: HTTP 요청을 수신한 경우]
   │
[범위: 처리]
   │
   ├ 스위치  켜기: @{coalesce(triggerBody()?['action'], '')}
   │
   ├─ case "ping"
   │     응답_핑: 200
   │     { "ok": true, "runId": "@{workflow()?['run']?['name']}",
   │       "echo": @{triggerBody()} }
   │
   ├─ case "upsert"
   │     작성_행수: @{length(coalesce(triggerBody()?['rows'], createArray()))}
   │     (여기에 나중에 실제 저장 로직을 넣습니다 → 12.3)
   │     응답_업서트: 200
   │     { "ok": true, "runId": "@{workflow()?['run']?['name']}",
   │       "count": @{outputs('작성_행수')} }
   │
   ├─ case "query"
   │     작성_샘플데이터:
   │       [ { "제목": "샘플 1", "상태": "대기", "금액": 1000 },
   │         { "제목": "샘플 2", "상태": "대기", "금액": 2500 } ]
   │     응답_조회: 200
   │     { "ok": true, "runId": "@{workflow()?['run']?['name']}",
   │       "data": @{outputs('작성_샘플데이터')} }
   │
   └─ 기본값
         응답_잘못된요청: 400
         { "ok": false, "error": "지원하지 않는 action: @{coalesce(triggerBody()?['action'], '(없음)')}" }
   │
[범위: 오류]   ⋯ → 실행 구성 = 실패한 경우 ✔ / 시간이 초과된 경우 ✔ / 건너뛴 경우 ✔
   ├ 응답_서버오류: 500
   │   { "ok": false, "runId": "@{workflow()?['run']?['name']}",
   │     "error": "플로우 실행 중 오류가 발생했습니다. 실행 기록을 확인하세요." }
   └ 종료: 상태 Failed
```

만들 때 주의:

- 각 case의 **응답 액션 이름을 다르게** 하세요(같은 이름 불가). 스위치의 한 실행에서는 하나만 실행되므로 충돌하지 않습니다.
- `[범위: 오류]`는 스위치 **다음**에 두고, ⋯ → **실행 구성**에서 세 항목을 체크합니다(6.2절 try/catch).
- 저장 후 `예제2_표전송`(count 응답)과 `예제3_조회후쓰기`(샘플 2행이 새 시트에 기록)로 확인합니다.

## 12.3 실제 데이터 붙이기 (예: SharePoint 목록)

`upsert` case의 `작성_행수` 앞에 추가:

```
각각에 적용  출력: @{triggerBody()?['rows']}
   └ SharePoint - 항목 만들기
        사이트 주소 / 목록 이름 선택
        제목:  @{items('각각에_적용')?['제목']}
        상태:  @{items('각각에_적용')?['상태']}
        금액:  @{items('각각에_적용')?['금액']}
```

`query` case는 `작성_샘플데이터`를 지우고:

```
SharePoint - 항목 가져오기   필터 쿼리: 상태 eq '대기'
선택(Select)
   시작: @{body('항목_가져오기')?['value']}
   맵:  제목 → @{item()?['Title']}   상태 → @{item()?['상태']?['Value']}   금액 → @{item()?['금액']}
응답_조회의 "data": @{body('선택')}
```

- 엑셀 머리글 이름과 `items(...)?['...']`의 키가 **정확히 일치**해야 합니다(VBA가 머리글을 키로 씁니다).
- 중복 실행 차단까지 하려면: 목록에 `requestId` 열을 만들고, 만들기 전에
  `항목 가져오기(필터: requestId eq '@{triggerBody()?['requestId']}')` → 있으면
  `{ "ok": true, "duplicate": true }` 200 응답으로 조기 종료(11.5절).

## 12.4 마무리 점검

- [ ] 예제 1·2·3이 모두 성공한다
- [ ] 일부러 `action`을 빼고 보내면 400 `ok:false`가 온다 (`PA_CallRaw`로 확인)
- [ ] 플로우 실행 기록에서 각 분기가 의도대로 탔는지 확인했다
- [ ] URL을 코드·셀에 붙여넣지 않았다 (`PA_SetFlowUrl`만 사용)
- [ ] 운영에 쓸 거라면: 솔루션 안으로 이동(8장), 오류 알림 추가(6장), 트리거 SAS 재생성 절차 숙지(11.7절)

---

이전: [11. VBA 연동](11-vba-integration.md) · [목차](README.md)
