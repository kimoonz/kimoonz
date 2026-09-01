# 9. 실전 레시피

바로 가져다 쓸 수 있는 패턴 모음입니다.

## 9.1 안전한 플로우 골격

새 플로우를 만들 때 이 구조로 시작하세요.

```
[트리거]  (트리거 조건 설정)
   │
[범위: 검증]
   ├ 필수값 확인
   └ 조건 → 미충족 시 응답 400 + 종료(Failed)
   │
[범위: 처리]
   ├ 데이터 조회 (OData 필터 + 열 제한)
   ├ 선택 / 배열 필터링으로 가공
   └ 대상 시스템에 반영
   │
[범위: 오류]   실행 구성 = 실패/시간초과/건너뜀
   ├ 작성: 오류요약 = result('처리') 에서 실패 항목 추출
   ├ 알림 (공용 메일 / Teams 채널)
   ├ 응답 500
   └ 종료(Failed)
   │
[응답 200]     실행 구성 = 성공한 경우
```

## 9.2 승인 요청 후 분기

```
트리거: SharePoint 항목이 만들어지는 경우
   │
승인 시작 및 대기
   승인 유형: 승인/거부 - 첫 번째 응답
   제목: @{concat('[결재] ', triggerBody()?['Title'])}
   할당 대상: @{triggerBody()?['승인자']?['Email']}
   세부 정보: 금액 @{formatNumber(int(triggerBody()?['금액']), '#,##0')}원
   항목 링크: @{triggerBody()?['{Link}']}
   │
스위치: @{body('승인_시작_및_대기')?['outcome']}
   ├ case 'Approve' → 항목 상태 '승인'으로 업데이트 + 요청자에게 메일
   ├ case 'Reject'  → 상태 '반려' + 사유 기록
   └ 기본값         → 종료(Failed, '알 수 없는 승인 결과')
```

응답자 코멘트: `@{first(body('승인_시작_및_대기')?['responses'])?['comments']}`

> 승인 액션은 최대 30일까지 대기합니다. 기한이 필요하면
> `승인 만들기` + `Do until`(타임아웃 포함) 조합으로 직접 구성해야 합니다.

## 9.3 매일 요약 메일 보내기

```
트리거: 되풀이 (1일, 표준 시간대 = (UTC+09:00) 서울, 기본 시간 08:00, 월~금)
   │
작성 (어제_시작):
   @{convertToUtc(startOfDay(addDays(convertTimeZone(utcNow(),'UTC','Korea Standard Time'), -1)), 'Korea Standard Time')}
작성 (오늘_시작):
   @{convertToUtc(startOfDay(convertTimeZone(utcNow(),'UTC','Korea Standard Time')), 'Korea Standard Time')}
   │
SharePoint 항목 가져오기
   필터 쿼리: Created ge '@{outputs('작성_어제_시작')}' and Created lt '@{outputs('작성_오늘_시작')}'
   열 제한: Title, 담당자, 금액, Created
   │
조건: @greater(length(body('항목_가져오기')?['value']), 0)
  ├ 예:
  │   선택 (표시용):
  │     제목 → @{item()?['Title']}
  │     담당자 → @{item()?['담당자']?['DisplayName']}
  │     금액 → @{formatNumber(int(item()?['금액']), '#,##0')}
  │     등록시각 → @{convertTimeZone(item()?['Created'],'UTC','Korea Standard Time','HH:mm')}
  │   HTML 테이블 만들기 ← 선택 결과
  │   메일 보내기
  │     제목: [일일요약] @{convertTimeZone(utcNow(),'UTC','Korea Standard Time','yyyy-MM-dd')} 신규 @{length(body('항목_가져오기')?['value'])}건
  │     본문: @{body('HTML_테이블_만들기')}
  └ 아니요: 종료(Succeeded)  ← 0건이면 메일 안 보냄
```

날짜 경계를 KST 기준으로 계산하는 부분이 핵심입니다(4.9절 참고).

## 9.4 HTTP API 만들기 (외부에서 호출 가능한 플로우)

VBA·사내 서버·웹훅이 호출할 엔드포인트를 만듭니다. 자세한 호출 코드는 [11장](11-vba-integration.md).

```
트리거: HTTP 요청 수신 시
   요청 본문 JSON 스키마:
   {
     "type": "object",
     "properties": {
       "action": { "type": "string" },
       "rows":   { "type": "array" }
     },
     "required": ["action"]
   }
   │
[범위: 검증]
   조건: @or(equals(triggerBody()?['action'],'upsert'), equals(triggerBody()?['action'],'query'))
     → 아니요: 응답 400 {"ok":false,"error":"지원하지 않는 action"} + 종료(Failed)
   │
[범위: 처리]
   스위치: @{triggerBody()?['action']}
     ├ 'upsert' → 각각에 적용(rows) → 항목 만들기
     └ 'query'  → 항목 가져오기 → 선택
   │
[범위: 오류]  실행 구성 = 실패/시간초과/건너뜀
   응답 500 { "ok": false, "error": "@{outputs('작성_오류요약')}" }
   종료(Failed)
   │
응답 200
   { "ok": true, "runId": "@{workflow()?['run']?['name']}", "count": @{...} }
```

**설계 지침**

- 응답은 항상 **같은 모양**으로 (`ok`, `error`, `data`). 호출자가 파싱하기 쉬워집니다.
- 성공/실패 모두 `runId`를 넣으면 장애 추적이 쉬워집니다.
- 처리가 120초를 넘길 것 같으면 **즉시 202를 반환**하고 결과는 별도 채널(메일/목록)로 알립니다.

## 9.5 대용량 목록을 나눠 처리하기

한 번에 다 처리하려 하지 말고, 처리 플래그로 나눕니다.

```
트리거: 되풀이 (15분마다)
   │
SharePoint 항목 가져오기
   필터 쿼리: 처리상태 eq '대기'
   상위 개수: 200
   │
조건: @empty(body('항목_가져오기')?['value'])
  → 예: 종료(Succeeded)
   │
각각에 적용 (동시성 5)
   ├ [범위: 항목처리]
   │    실제 작업
   ├ 항목 업데이트: 처리상태 = '완료'      실행 구성 = 성공한 경우
   └ 항목 업데이트: 처리상태 = '오류',
                   오류메시지 = @{string(result('항목처리'))}
                                              실행 구성 = 실패한 경우
```

- 실패한 항목만 남으므로 재실행이 안전합니다(**멱등성**).
- 처리 상태 열은 **인덱싱**하세요(5,000 임계값 회피).

## 9.6 재시도 + 실패 알림 래퍼

외부 API가 불안정할 때 쓰는 패턴입니다.

```
변수 초기화: 시도 = 0, 성공 = false
   │
Do until: @or(variables('성공'), greaterOrEquals(variables('시도'), 3))
   ├ [범위: 호출]
   │    HTTP (재시도 정책: 없음, 시간 제한 PT30S)
   ├ 변수 설정: 성공 = true          실행 구성 = 성공한 경우
   ├ 변수 증가: 시도 + 1
   └ 지연: @{mul(variables('시도'), 5)} 초    실행 구성 = 성공/실패 모두
   │
조건: @variables('성공')
  └ 아니요: 알림 + 종료(Failed, '3회 재시도 후 실패')
```

액션 자체의 재시도 정책으로 충분한 경우가 많습니다.
이 패턴은 **재시도 사이에 다른 작업(토큰 재발급 등)이 필요할 때** 씁니다.

## 9.7 반복 없이 배열 다루기

```
목표: 승인된 항목의 금액 합계와 담당자 목록

배열 필터링 (승인건)
   시작: @{body('항목_가져오기')?['value']}
   조건: @equals(item()?['상태']?['Value'], '승인')

선택 (금액목록)
   시작: @{body('배열_필터링_승인건')}
   맵(텍스트 모드): @{int(item()?['금액'])}

작성 (합계)
   @{if(empty(body('선택_금액목록')), 0,
        add(0, ...))}
```

> 합계는 표현식만으로는 깔끔하지 않습니다. 실무에서는 다음 중 하나를 씁니다.
> 1. `각각에 적용`(동시성 1) + 변수 증가 — 항목이 적을 때
> 2. Office 스크립트 / Azure Function에 배열을 넘겨 계산 — 항목이 많을 때
> 3. 원본에서 집계해 오기 (SQL의 `SUM`, Dataverse 롤업 열) — **가장 권장**
>
> 집계는 Power Automate가 잘하는 일이 아닙니다. 데이터 원본에 맡기세요.

담당자 목록(중복 제거):

```
선택 (담당자들): @{item()?['담당자']?['DisplayName']}
작성 (고유담당자): @{union(body('선택_담당자들'), body('선택_담당자들'))}
작성 (표시): @{join(outputs('작성_고유담당자'), ', ')}
```

## 9.8 첨부 파일 저장

```
트리거: 새 메일이 도착하면 (첨부 파일 포함 = 예, 첨부 파일 있음 = 예)
   │
각각에 적용: @{triggerOutputs()?['body/attachments']}
   ├ 조건: @contains(createArray('pdf','xlsx','docx'),
   │                toLower(last(split(items('각각에_적용')?['name'], '.'))))
   └ 예: SharePoint 파일 만들기
          파일 이름: @{concat(
             convertTimeZone(triggerOutputs()?['body/receivedDateTime'],'UTC','Korea Standard Time','yyyyMMdd_HHmmss'),
             '_', items('각각에_적용')?['name'])}
          파일 콘텐츠: @{items('각각에_적용')?['contentBytes']}
```

파일명에 타임스탬프를 붙여 덮어쓰기를 막습니다.

## 9.9 감사 로그 남기기

실행 기록은 28일만 보관됩니다. 장기 보관이 필요하면 직접 적재하세요.

```
[범위: 처리] 이후

항목 만들기 (감사로그 목록)
   실행일시: @{utcNow()}
   플로우명: @{workflow()?['name']}
   실행ID:   @{workflow()?['run']?['name']}
   결과:     성공 / 실패
   입력:     @{substring(string(triggerBody()), 0, min(4000, length(string(triggerBody()))))}
   메시지:   @{outputs('작성_오류요약')}
```

성공 경로와 오류 경로 양쪽에 두되, 오류 경로에서는 `결과`를 '실패'로 씁니다.
입력값은 길이를 잘라 넣어야 텍스트 열 제한에 걸리지 않습니다.

> 개인정보가 들어 있는 입력을 그대로 로그에 남기지 마세요. 필요한 식별자만 남기거나 마스킹하세요.

---

이전: [8. 솔루션과 배포](08-alm-deployment.md) · 다음: [10. 문제 해결](10-troubleshooting.md)
