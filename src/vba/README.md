# VBA 모듈

Excel VBA에서 Power Automate 플로우를 호출하고, 차트를 PowerPoint로 자동 반영하는 모듈입니다.
**참조 추가 없이**(후기 바인딩) 32/64비트 Office에서 동작합니다.

| 파일 | 역할 | 의존성 |
| --- | --- | --- |
| `JsonLite.bas` | JSON 파싱/직렬화 (한글·이스케이프·로캘 안전) | 없음 |
| `PowerAutomate.bas` | 플로우 HTTP 호출, 재시도, UTF-8, 범위↔JSON, URL 보관 | JsonLite |
| `ExcelToPowerPoint.bas` | 차트/표 → PPT 슬라이드 생성·갱신 | 없음 (PowerPoint 설치 필요) |
| `Examples.bas` | 실행 가능한 예제 8종 | 위 전부 |

## 설치

1. Excel에서 Alt+F11 → 파일 → **파일 가져오기** → 필요한 `.bas` 선택
2. 매크로 사용 통합 문서(`.xlsm`)로 저장
3. 즉시 실행 창(Ctrl+G)에서 플로우 URL 등록:
   ```vba
   PA_SetFlowUrl "기본플로우", "<플로우 HTTP POST URL>"
   ```
4. `예제1_단건전송` 실행으로 연결 확인

## 빠른 사용법

```vba
' 표 전송
Dim p As Object: Set p = JsonObject()
p.Add "action", "upsert"
p.Add "rows", PA_RangeToArray(Range("A1").CurrentRegion)
Dim res As Object: Set res = PA_Call("기본플로우", p)

' 응답을 시트에
PA_ArrayToRange JsonGet(res, "data"), Sheets("결과").Range("A1")

' 차트 전부 PPT로
XP_ExportAllCharts savePath:=ThisWorkbook.Path & "\보고.pptx"

' 기존 PPT 틀의 그림만 최신 차트로 교체
XP_RefreshLinkedPictures ThisWorkbook.Path & "\보고.pptx"
```

자세한 설명: [../../docs/power-automate/11-vba-integration.md](../../docs/power-automate/11-vba-integration.md)

## 보안

플로우 URL은 서명이 포함된 자격 증명입니다. `PA_SetFlowUrl`은 통합 문서가 아닌
사용자 레지스트리(HKCU)에 저장하므로 파일 공유 시 URL이 따라가지 않습니다.
단, 암호화는 아니므로 강한 보호가 필요하면 사내 프록시를 경유하세요.
