Attribute VB_Name = "Examples"
'==============================================================================
' Examples - PowerAutomate / ExcelToPowerPoint 모듈 사용 예제
' 즉시 실행 창(Ctrl+G)이나 매크로 목록에서 하나씩 실행해 보세요.
'==============================================================================
Option Explicit

'------------------------------------------------------------------------------
' 0. 최초 1회: 플로우 URL 등록 (통합 문서에는 저장되지 않습니다)
'------------------------------------------------------------------------------
Public Sub 예제0_플로우URL설정()
    Dim url As String
    url = InputBox("플로우의 HTTP POST URL을 붙여넣으세요." & vbCrLf & _
                   "(플로우의 'HTTP 요청 수신 시' 트리거를 저장하면 생성됩니다)", "플로우 URL 등록")
    If Len(Trim$(url)) = 0 Then Exit Sub
    PA_SetFlowUrl "기본플로우", Trim$(url)
    MsgBox "저장했습니다. 이제 예제1을 실행해 보세요.", vbInformation
End Sub

'------------------------------------------------------------------------------
' 1. 단일 데이터 보내기 - 가장 단순한 호출
'------------------------------------------------------------------------------
Public Sub 예제1_단건전송()
    Dim payload As Object
    Set payload = JsonObject()
    payload.Add "requestId", PA_NewRequestId()
    payload.Add "action", "ping"
    payload.Add "보낸사람", Application.UserName
    payload.Add "메모", "한글도 그대로 전달됩니다 ✓"

    On Error GoTo Fail
    Dim res As Object
    Set res = PA_Call("기본플로우", payload)

    MsgBox "성공!" & vbCrLf & "응답: " & JsonStringify(res, True), vbInformation
    Exit Sub

Fail:
    MsgBox Err.Description, vbExclamation, "플로우 호출 실패"
End Sub

'------------------------------------------------------------------------------
' 2. 시트의 표를 통째로 보내기
'    현재 시트 A1부터의 표(첫 행=머리글)를 JSON 배열로 전송합니다.
'------------------------------------------------------------------------------
Public Sub 예제2_표전송()
    Dim rng As Range
    Set rng = ActiveSheet.Range("A1").CurrentRegion
    If rng.Rows.Count < 2 Then
        MsgBox "A1부터 머리글 + 데이터가 있는 표가 필요합니다.", vbExclamation
        Exit Sub
    End If

    Dim payload As Object
    Set payload = JsonObject()
    payload.Add "requestId", PA_NewRequestId()
    payload.Add "action", "upsert"
    payload.Add "rows", PA_RangeToArray(rng)

    On Error GoTo Fail
    Dim res As Object
    Set res = PA_Call("기본플로우", payload)
    MsgBox rng.Rows.Count - 1 & "행을 보냈습니다." & vbCrLf & _
           "응답: " & JsonStringify(res), vbInformation
    Exit Sub

Fail:
    MsgBox Err.Description, vbExclamation, "전송 실패"
End Sub

'------------------------------------------------------------------------------
' 3. 조회 결과를 시트에 받아 쓰기
'    플로우가 { "data": [ {...}, {...} ] } 형태로 응답한다고 가정합니다.
'------------------------------------------------------------------------------
Public Sub 예제3_조회후쓰기()
    Dim payload As Object
    Set payload = JsonObject()
    payload.Add "action", "query"
    payload.Add "상태", "대기"

    On Error GoTo Fail
    Dim res As Object
    Set res = PA_Call("기본플로우", payload)

    Dim rows As Variant
    rows = JsonGet(res, "data")
    If IsEmpty(rows) Or Not IsObject(rows) Then
        MsgBox "응답에 data 배열이 없습니다: " & JsonStringify(res), vbExclamation
        Exit Sub
    End If

    Dim ws As Worksheet
    Set ws = ThisWorkbook.Worksheets.Add
    Dim written As Long
    written = PA_ArrayToRange(rows, ws.Range("A1"))
    ws.Columns.AutoFit
    MsgBox written & "행을 받았습니다.", vbInformation
    Exit Sub

Fail:
    MsgBox Err.Description, vbExclamation, "조회 실패"
End Sub

'------------------------------------------------------------------------------
' 4. 상태/오류를 직접 다루고 싶을 때 (PA_CallRaw)
'------------------------------------------------------------------------------
Public Sub 예제4_저수준호출()
    Dim payload As Object
    Set payload = JsonObject()
    payload.Add "action", "ping"

    Dim resp As PAResponse
    resp = PA_CallRaw("기본플로우", payload, maxRetry:=3)

    Debug.Print "상태코드:", resp.StatusCode
    Debug.Print "시도횟수:", resp.Attempts
    Debug.Print "소요(ms):", resp.ElapsedMs
    Debug.Print "실행 ID:", resp.RunId
    Debug.Print "본문:", resp.Body

    If Not resp.Success Then
        MsgBox PA_DescribeError(resp), vbExclamation
    End If
End Sub

'------------------------------------------------------------------------------
' 5. 모든 차트를 PowerPoint 로 내보내기
'------------------------------------------------------------------------------
Public Sub 예제5_차트를PPT로()
    On Error GoTo Fail
    XP_ExportAllCharts
    Exit Sub
Fail:
    MsgBox Err.Description, vbExclamation, "PPT 내보내기 실패"
End Sub

'------------------------------------------------------------------------------
' 6. 회사 템플릿에 얹어서 파일로 저장까지
'------------------------------------------------------------------------------
Public Sub 예제6_템플릿으로PPT저장()
    Dim savePath As String
    savePath = ThisWorkbook.Path & "\주간보고_" & Format$(Date, "yyyymmdd") & ".pptx"

    On Error GoTo Fail
    XP_ExportAllCharts templatePath:="", savePath:=savePath   ' templatePath에 .potx 경로 지정 가능
    MsgBox "저장했습니다: " & savePath, vbInformation
    Exit Sub
Fail:
    MsgBox Err.Description, vbExclamation, "PPT 저장 실패"
End Sub

'------------------------------------------------------------------------------
' 7. 기존 보고서 PPT의 그림만 최신 차트로 교체 (매주 갱신용)
'------------------------------------------------------------------------------
Public Sub 예제7_기존PPT갱신()
    Dim pptPath As String
    pptPath = ThisWorkbook.Path & "\주간보고.pptx"
    If Len(Dir(pptPath)) = 0 Then
        MsgBox "파일이 없습니다: " & pptPath & vbCrLf & _
               "먼저 예제6으로 만든 파일을 이 이름으로 두세요.", vbExclamation
        Exit Sub
    End If

    On Error GoTo Fail
    XP_RefreshLinkedPictures pptPath
    MsgBox "차트 그림을 최신으로 교체했습니다.", vbInformation
    Exit Sub
Fail:
    MsgBox Err.Description, vbExclamation, "갱신 실패"
End Sub

'------------------------------------------------------------------------------
' 8. 전체 파이프라인: 플로우에서 데이터 조회 → 시트 갱신 → 차트 → PPT
'------------------------------------------------------------------------------
Public Sub 예제8_데이터에서PPT까지()
    On Error GoTo Fail

    ' 1) 플로우에서 데이터 받기
    Dim payload As Object
    Set payload = JsonObject()
    payload.Add "action", "query"

    Dim res As Object
    Set res = PA_Call("기본플로우", payload)

    Dim rows As Variant
    rows = JsonGet(res, "data")
    If Not IsObject(rows) Then Err.Raise vbObjectError + 1, , "응답에 data 배열이 없습니다."

    ' 2) '데이터' 시트에 쓰기 (차트가 이 시트를 원본으로 참조하고 있으면 자동 갱신됨)
    Dim ws As Worksheet
    Set ws = ThisWorkbook.Worksheets("데이터")
    ws.Cells.ClearContents
    PA_ArrayToRange rows, ws.Range("A1")

    ' 3) 차트가 다시 계산되도록
    Application.Calculate
    DoEvents

    ' 4) PPT로
    Dim savePath As String
    savePath = ThisWorkbook.Path & "\자동보고_" & Format$(Now, "yyyymmdd_hhnn") & ".pptx"
    XP_ExportAllCharts savePath:=savePath
    MsgBox "완료: " & savePath, vbInformation
    Exit Sub

Fail:
    MsgBox Err.Description, vbExclamation, "파이프라인 실패"
End Sub
