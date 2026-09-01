# 11. VBA ↔ Power Automate 연동

Excel VBA에서 클라우드 플로우를 호출하고 결과를 돌려받는 방법입니다.
이 저장소의 [`src/vba/`](../../src/vba/) 모듈이 실제 동작 코드를 제공합니다.

## 11.1 전체 그림

```
Excel VBA                       Power Automate                  대상 시스템
────────────                    ────────────────                ────────────
PA_Call("주문등록", payload)
  │  POST (JSON, UTF-8)
  ├──────────────────────────▶  [HTTP 요청 수신 시] 트리거
  │                               ├ 검증 → 400 응답
  │                               ├ 처리 ─────────────────────▶ SharePoint / Teams
  │                               ├ 오류 → 500 응답               SQL / 메일 / 승인 …
  │  ◀───────────────────────── [응답] 200 {"ok":true,...}
  ▼
응답 Dictionary 로 후속 처리
```

- 필요 라이선스: HTTP 트리거는 **프리미엄**입니다(1.4절).
- 동기 응답 한도는 약 **120초**입니다. 오래 걸리는 플로우는 202 비동기 패턴(11.6절).

## 11.2 플로우 쪽 준비

1. 인스턴트 클라우드 플로우 → 트리거 **HTTP 요청 수신 시**
2. 요청 본문 JSON 스키마 지정 — 샘플: [`samples/http-trigger-schema.json`](../../samples/http-trigger-schema.json)
3. 9.4절의 골격대로 검증 → 처리 → 오류 → **응답** 구성
   (오류 경로에도 응답 액션이 있어야 VBA가 타임아웃 대신 명확한 오류를 받습니다)
4. 저장하면 **HTTP POST URL**이 생성됩니다 — 이걸 VBA에 등록합니다.

응답은 항상 같은 모양으로 통일하세요:

```json
{ "ok": true,  "runId": "...", "data": [ ... ] }
{ "ok": false, "runId": "...", "error": "사유" }
```

## 11.3 VBA 쪽 준비

1. VBE(Alt+F11) → 파일 가져오기로 `JsonLite.bas`, `PowerAutomate.bas`(필요 시 `Examples.bas`, `ExcelToPowerPoint.bas`) 임포트
2. 참조 추가 불필요 — 전부 후기 바인딩입니다.
3. 즉시 실행 창(Ctrl+G)에서 URL 등록:

```vba
PA_SetFlowUrl "주문등록", "https://prod-00....logic.azure.com/workflows/...?...&sig=..."
```

URL은 통합 문서가 아니라 **현재 사용자 레지스트리**에 저장됩니다.
파일을 공유해도 URL(=자격 증명)이 함께 나가지 않습니다.

## 11.4 호출하기

```vba
Dim payload As Object
Set payload = JsonObject()
payload.Add "requestId", PA_NewRequestId()      ' 중복 실행 방지용
payload.Add "action", "upsert"
payload.Add "rows", PA_RangeToArray(Sheet1.Range("A1").CurrentRegion)

Dim res As Object
Set res = PA_Call("주문등록", payload)           ' 실패 시 오류 발생
Debug.Print res("ok"), JsonGet(res, "data/0/제목")
```

상태 코드·재시도 횟수까지 직접 다루려면 `PA_CallRaw` / `PA_Post`가 `PAResponse` 구조체를 돌려줍니다.
더 많은 예제는 [`src/vba/Examples.bas`](../../src/vba/Examples.bas).

## 11.5 왜 이 모듈을 쓰는가 (직접 짤 때의 함정)

| 함정 | 이 모듈의 처리 |
| --- | --- |
| 한글이 `???`나 깨진 문자로 도착 | 요청/응답 모두 UTF-8을 직접 인코딩/디코딩 |
| 따옴표·줄바꿈이 든 값이 JSON을 깨뜨림 | JsonLite 직렬화가 모든 이스케이프 처리 |
| 소수점이 로캘에 따라 `,`로 직렬화됨 | 로캘 무관 숫자 표기(`Str$` 기반) |
| 순간적인 네트워크 오류로 실패 | 지수 백오프 재시도(기본: 중복 위험 없는 경우만) |
| 재시도로 플로우가 두 번 실행됨 | 5xx 재시도는 기본 꺼짐 + `requestId`로 플로우 쪽 중복 차단 |
| 긴 플로우에서 타임아웃 | 202 + Location 폴링 지원 |
| 64비트 Office에서 컴파일 오류 | `PtrSafe` 조건부 선언 |

플로우 쪽 중복 차단: 처리 전에 `requestId`를 목록/테이블에서 조회해 이미 있으면
`200 {"ok":true,"duplicate":true}`로 즉시 응답하게 만드세요.

## 11.6 202 비동기 패턴

플로우가 120초 안에 못 끝나면:

- 플로우: 트리거 직후 즉시 202 `응답`을 보내고 본 처리를 계속
- VBA: `PA_Post(url, body, waitForAsync:=True)` — 202의 `Location` 헤더를
  완료될 때까지 폴링합니다(기본 최대 600초)

결과를 꼭 받아야 하는 게 아니라면, 결과는 메일/Teams로 보내게 하고 VBA는 접수 확인만 받는 편이 단순합니다.

## 11.7 보안 체크리스트

- [ ] 플로우 URL을 **셀·코드에 하드코딩하지 않는다** (`PA_SetFlowUrl` / 환경 변수 사용)
- [ ] URL이 유출된 것 같으면 트리거를 다시 저장해 **서명을 재생성**한다 (기존 URL 즉시 무효화)
- [ ] 플로우 첫 단계에서 payload의 필수 필드·값 범위를 검증한다
- [ ] 사내 공용이라면 URL 배포 대신 **사내 프록시/Azure API Management**를 앞에 두는 것을 검토
- [ ] 레지스트리 저장은 암호화가 아니라 격리일 뿐임을 이해하고 사용

## 11.8 보너스: 결과를 PowerPoint 보고서로

`ExcelToPowerPoint.bas`를 함께 임포트하면, 플로우에서 받은 데이터로 갱신된 차트를
PPT 슬라이드로 자동 내보낼 수 있습니다.

```vba
' 데이터 수신 → 시트 갱신 → 차트 자동 갱신 → PPT 생성까지 한 번에
예제8_데이터에서PPT까지                       ' Examples.bas 참고

XP_ExportAllCharts savePath:="C:\보고\주간보고.pptx"      ' 모든 차트를 슬라이드로
XP_RefreshLinkedPictures "C:\보고\주간보고.pptx"          ' 기존 PPT의 그림만 최신 차트로 교체
```

`XP_RefreshLinkedPictures`는 이 모듈이 넣은 그림에 원본 차트 태그를 붙여 두었다가,
같은 위치·크기로 최신 그림으로 갈아 끼웁니다 — 매주 같은 보고서 틀을 재사용할 때 유용합니다.

---

이전: [10. 문제 해결](10-troubleshooting.md) · [목차](README.md)
