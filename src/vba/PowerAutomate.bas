Attribute VB_Name = "PowerAutomate"
'==============================================================================
' PowerAutomate - Excel VBA에서 Power Automate 클라우드 플로우를 호출하는 모듈
'------------------------------------------------------------------------------
' 필요 조건
'   - 대상 플로우의 트리거가 "HTTP 요청 수신 시"(프리미엄 커넥터)
'   - JsonLite.bas 모듈이 같은 프로젝트에 함께 임포트되어 있을 것
'   - 참조 추가 불필요 (모두 후기 바인딩)
'   - 32비트 · 64비트 Office 모두 지원
'
' 주요 기능
'   - UTF-8 인코딩/디코딩을 직접 처리하여 한글이 깨지지 않음
'   - 재시도(지수 백오프) - 기본은 "중복 실행이 확실히 없는 경우"만 재시도
'   - 202 Accepted 비동기 응답 폴링
'   - 플로우 URL을 통합 문서 밖(레지스트리/환경 변수)에 보관
'   - Excel 범위 <-> JSON 배열 변환
'
' 보안 주의
'   HTTP 트리거 URL에는 서명(sig=)이 들어 있어 그 자체가 자격 증명입니다.
'   통합 문서에 하드코딩해서 배포하지 마세요. PA_SetFlowUrl 로 저장하면
'   HKCU 레지스트리에 보관됩니다(암호화 아님, 사용자 계정 단위 격리).
'   더 강한 보호가 필요하면 사내 프록시나 Azure API Management를 앞에 두세요.
'==============================================================================
Option Explicit

#If VBA7 Then
    Private Declare PtrSafe Sub apiSleep Lib "kernel32" Alias "Sleep" (ByVal dwMilliseconds As Long)
#Else
    Private Declare Sub apiSleep Lib "kernel32" Alias "Sleep" (ByVal dwMilliseconds As Long)
#End If

Private Const ERR_BASE As Long = vbObjectError + 2000

' 플로우 URL 저장 위치 (HKCU\Software\VB and VBA Program Settings\...)
Private Const REG_APP As String = "PowerAutomateVBA"
Private Const REG_SECTION As String = "Flows"

' URL을 워크시트에 두고 싶을 때 쓰는 시트 이름 (A열=키, B열=URL)
Private Const CONFIG_SHEET As String = "PA_Config"

' 기본값
Private Const DEFAULT_TIMEOUT_SEC As Long = 130      ' 동기 응답 한도(약 120초)보다 조금 크게
Private Const DEFAULT_MAX_RETRY As Long = 2
Private Const DEFAULT_ASYNC_TIMEOUT_SEC As Long = 600
Private Const ASYNC_POLL_INTERVAL_MS As Long = 3000

'==============================================================================
' 응답 구조체
'==============================================================================
Public Type PAResponse
    Success As Boolean          ' 2xx 응답이면 True
    StatusCode As Long          ' HTTP 상태 코드 (0 = 응답을 받지 못함)
    Body As String              ' 응답 본문 (UTF-8 디코딩됨)
    Data As Object              ' 본문이 JSON 객체/배열이면 파싱 결과, 아니면 Nothing
    Headers As String           ' 응답 헤더 전체
    RunId As String             ' x-ms-workflow-run-id (플로우 실행 추적용)
    Location As String          ' 202 응답의 Location 헤더
    ErrorMessage As String      ' 실패 사유
    Attempts As Long            ' 실제 시도 횟수
    ElapsedMs As Long           ' 소요 시간
End Type

'==============================================================================
' 플로우 URL 관리
'==============================================================================

' 플로우 URL을 저장합니다. (통합 문서가 아니라 현재 사용자 레지스트리에 보관)
'   PA_SetFlowUrl "주문등록", "https://prod-00.koreacentral.logic.azure.com:443/workflows/..."
Public Sub PA_SetFlowUrl(ByVal flowKey As String, ByVal url As String)
    If Len(Trim$(flowKey)) = 0 Then
        Err.Raise ERR_BASE + 1, "PowerAutomate", "flowKey를 비워 둘 수 없습니다."
    End If
    SaveSetting REG_APP, REG_SECTION, flowKey, url
End Sub

' 저장된 플로우 URL을 지웁니다.
Public Sub PA_ClearFlowUrl(ByVal flowKey As String)
    On Error Resume Next
    DeleteSetting REG_APP, REG_SECTION, flowKey
    On Error GoTo 0
End Sub

' 플로우 URL을 찾습니다. 우선순위: 레지스트리 > 환경 변수 > PA_Config 시트
'   환경 변수 이름은 PA_FLOW_<대문자키> 입니다. 예) PA_FLOW_주문등록
Public Function PA_GetFlowUrl(ByVal flowKey As String) As String
    Dim url As String

    url = GetSetting(REG_APP, REG_SECTION, flowKey, "")
    If Len(url) = 0 Then url = Environ$("PA_FLOW_" & UCase$(flowKey))
    If Len(url) = 0 Then url = ReadConfigSheet(flowKey)

    If Len(url) = 0 Then
        Err.Raise ERR_BASE + 2, "PowerAutomate", _
            "'" & flowKey & "' 플로우의 URL이 설정되지 않았습니다." & vbCrLf & _
            "다음 중 하나로 설정하세요:" & vbCrLf & _
            "  1) 즉시 실행 창에서:  PA_SetFlowUrl """ & flowKey & """, ""<플로우 HTTP POST URL>""" & vbCrLf & _
            "  2) 환경 변수:  PA_FLOW_" & UCase$(flowKey) & vbCrLf & _
            "  3) '" & CONFIG_SHEET & "' 시트의 A열=키, B열=URL"
    End If

    PA_GetFlowUrl = url
End Function

' PA_Config 시트에서 URL을 읽습니다. 시트가 없으면 빈 문자열.
Private Function ReadConfigSheet(ByVal flowKey As String) As String
    Dim ws As Object, r As Long, lastRow As Long

    On Error GoTo NoSheet
    Set ws = Application.ThisWorkbook.Worksheets(CONFIG_SHEET)

    lastRow = ws.Cells(ws.Rows.Count, 1).End(-4162).Row     ' -4162 = xlUp
    For r = 1 To lastRow
        If StrComp(Trim$(CStr(ws.Cells(r, 1).Value)), flowKey, vbTextCompare) = 0 Then
            ReadConfigSheet = Trim$(CStr(ws.Cells(r, 2).Value))
            Exit Function
        End If
    Next r
    Exit Function

NoSheet:
    ReadConfigSheet = ""
End Function

'==============================================================================
' 고수준 호출 API
'==============================================================================

' 가장 자주 쓰는 형태: Dictionary를 보내고 응답 JSON을 Dictionary로 받습니다.
' 실패하면 오류를 발생시키므로 호출부에서 On Error로 처리하세요.
'
'   Dim payload As Object: Set payload = JsonObject()
'   payload.Add "action", "upsert"
'   payload.Add "rows", PA_RangeToArray(Sheet1.Range("A1:D50"))
'   Dim res As Object
'   Set res = PA_Call("주문등록", payload)
'   Debug.Print res("count")
Public Function PA_Call(ByVal flowKey As String, ByVal payload As Variant) As Object
    Dim resp As PAResponse
    resp = PA_CallRaw(flowKey, payload)

    If Not resp.Success Then
        Err.Raise ERR_BASE + 3, "PowerAutomate", PA_DescribeError(resp)
    End If

    If resp.Data Is Nothing Then
        ' 본문이 비었거나 JSON이 아니면 상태만 담아 돌려줍니다.
        Dim d As Object
        Set d = JsonObject()
        d.Add "statusCode", resp.StatusCode
        d.Add "body", resp.Body
        d.Add "runId", resp.RunId
        Set PA_Call = d
    Else
        Set PA_Call = resp.Data
    End If
End Function

' PA_Call과 같지만 오류를 발생시키지 않고 PAResponse를 그대로 돌려줍니다.
' payload에는 Dictionary / Collection / JSON 문자열 중 아무거나 넣을 수 있습니다.
Public Function PA_CallRaw(ByVal flowKey As String, ByVal payload As Variant, _
                           Optional ByVal timeoutSec As Long = DEFAULT_TIMEOUT_SEC, _
                           Optional ByVal maxRetry As Long = DEFAULT_MAX_RETRY, _
                           Optional ByVal retryOn5xx As Boolean = False, _
                           Optional ByVal waitForAsync As Boolean = False) As PAResponse

    Dim jsonBody As String
    If VarType(payload) = vbString Then
        jsonBody = CStr(payload)
    Else
        jsonBody = JsonStringify(payload)
    End If

    PA_CallRaw = PA_Post(PA_GetFlowUrl(flowKey), jsonBody, _
                         timeoutSec, maxRetry, retryOn5xx, waitForAsync)
End Function

'==============================================================================
' 저수준 HTTP
'==============================================================================

' 플로우 URL로 JSON을 POST합니다.
'
' maxRetry / retryOn5xx 주의:
'   기본 재시도 대상은 "요청이 서버에 닿지 않은 것이 확실한 경우"뿐입니다.
'     - StatusCode 0 (연결 실패/타임아웃)
'     - 429 Too Many Requests (요청이 거부됨)
'   5xx는 플로우가 이미 실행되었을 수 있으므로 기본적으로 재시도하지 않습니다.
'   플로우가 멱등(같은 요청을 두 번 처리해도 안전)하다면 retryOn5xx:=True 로 켜세요.
Public Function PA_Post(ByVal url As String, ByVal jsonBody As String, _
                        Optional ByVal timeoutSec As Long = DEFAULT_TIMEOUT_SEC, _
                        Optional ByVal maxRetry As Long = DEFAULT_MAX_RETRY, _
                        Optional ByVal retryOn5xx As Boolean = False, _
                        Optional ByVal waitForAsync As Boolean = False, _
                        Optional ByVal asyncTimeoutSec As Long = DEFAULT_ASYNC_TIMEOUT_SEC) As PAResponse

    Dim resp As PAResponse
    Dim startTick As Single
    Dim attempt As Long
    Dim backoffMs As Long

    startTick = Timer

    If Len(Trim$(url)) = 0 Then
        resp.ErrorMessage = "플로우 URL이 비어 있습니다."
        PA_Post = resp
        Exit Function
    End If

    If maxRetry < 0 Then maxRetry = 0
    backoffMs = 1000

    Do
        attempt = attempt + 1
        resp = SendOnce("POST", url, jsonBody, "application/json; charset=utf-8", timeoutSec)
        resp.Attempts = attempt

        If resp.Success Then Exit Do
        If attempt > maxRetry Then Exit Do
        If Not ShouldRetry(resp.StatusCode, retryOn5xx) Then Exit Do

        WaitMs backoffMs
        backoffMs = backoffMs * 2
        If backoffMs > 16000 Then backoffMs = 16000
    Loop

    ' 202 Accepted: 플로우는 접수되었고 아직 실행 중입니다.
    If resp.StatusCode = 202 And waitForAsync And Len(resp.Location) > 0 Then
        resp = PollAsync(resp.Location, asyncTimeoutSec, resp)
        resp.Attempts = attempt
    End If

    resp.ElapsedMs = CLng((Timer - startTick) * 1000)
    PA_Post = resp
End Function

' 임의의 URL에 GET 요청을 보냅니다(플로우 상태 조회 등).
Public Function PA_Get(ByVal url As String, _
                       Optional ByVal timeoutSec As Long = 60) As PAResponse
    Dim startTick As Single
    startTick = Timer
    Dim resp As PAResponse
    resp = SendOnce("GET", url, "", "", timeoutSec)
    resp.Attempts = 1
    resp.ElapsedMs = CLng((Timer - startTick) * 1000)
    PA_Get = resp
End Function

' 202 응답의 Location을 완료될 때까지 폴링합니다.
Private Function PollAsync(ByVal location As String, ByVal timeoutSec As Long, _
                           ByRef accepted As PAResponse) As PAResponse
    Dim waited As Long
    Dim r As PAResponse

    Do
        WaitMs ASYNC_POLL_INTERVAL_MS
        waited = waited + ASYNC_POLL_INTERVAL_MS

        r = SendOnce("GET", location, "", "", 60)

        If r.StatusCode <> 202 And r.StatusCode <> 0 Then
            r.RunId = accepted.RunId
            PollAsync = r
            Exit Function
        End If

        If waited >= timeoutSec * 1000& Then
            accepted.Success = False
            accepted.ErrorMessage = "비동기 실행이 " & timeoutSec & "초 안에 끝나지 않았습니다. " & _
                                    "실행 ID=" & accepted.RunId
            PollAsync = accepted
            Exit Function
        End If
    Loop
End Function

' 실제 HTTP 요청 1회.
Private Function SendOnce(ByVal method As String, ByVal url As String, _
                          ByVal body As String, ByVal contentType As String, _
                          ByVal timeoutSec As Long) As PAResponse
    Dim resp As PAResponse
    Dim http As Object

    On Error GoTo Failed

    Set http = NewHttpClient()
    http.Open method, url, False

    If Len(contentType) > 0 Then http.setRequestHeader "Content-Type", contentType
    http.setRequestHeader "Accept", "application/json"

    ' resolve / connect / send / receive (밀리초)
    On Error Resume Next
    http.setTimeouts 20000, 20000, timeoutSec * 1000&, timeoutSec * 1000&
    On Error GoTo Failed

    If Len(body) > 0 Then
        http.send Utf8Bytes(body)
    Else
        http.send
    End If

    resp.StatusCode = CLng(http.Status)
    resp.Body = ReadResponseText(http)
    resp.Headers = SafeAllHeaders(http)
    resp.RunId = SafeHeader(http, "x-ms-workflow-run-id")
    resp.Location = SafeHeader(http, "Location")
    resp.Success = (resp.StatusCode >= 200 And resp.StatusCode < 300)

    If Not resp.Success Then
        resp.ErrorMessage = "HTTP " & resp.StatusCode & " " & Left$(resp.Body, 500)
    End If

    Set resp.Data = TryParseJson(resp.Body)

    SendOnce = resp
    Exit Function

Failed:
    resp.Success = False
    resp.StatusCode = 0
    resp.ErrorMessage = "요청 실패: " & Err.Number & " - " & Err.Description
    SendOnce = resp
End Function

Private Function NewHttpClient() As Object
    Dim h As Object

    On Error Resume Next
    Set h = CreateObject("MSXML2.ServerXMLHTTP.6.0")
    If h Is Nothing Then Set h = CreateObject("WinHttp.WinHttpRequest.5.1")
    If h Is Nothing Then Set h = CreateObject("MSXML2.ServerXMLHTTP")
    On Error GoTo 0

    If h Is Nothing Then
        Err.Raise ERR_BASE + 10, "PowerAutomate", _
            "HTTP 클라이언트를 만들 수 없습니다. MSXML6 또는 WinHTTP가 사용 가능한지 확인하세요."
    End If

    Set NewHttpClient = h
End Function

' 재시도해도 안전한 상황인지 판단합니다.
Private Function ShouldRetry(ByVal statusCode As Long, ByVal retryOn5xx As Boolean) As Boolean
    Select Case statusCode
        Case 0          ' 응답을 받지 못함 - 요청이 닿지 않았을 가능성이 높음
            ShouldRetry = True
        Case 429        ' 스로틀링 - 요청이 거부되었으므로 중복 실행 위험 없음
            ShouldRetry = True
        Case 408        ' 요청 타임아웃
            ShouldRetry = True
        Case 500, 502, 503, 504
            ShouldRetry = retryOn5xx
        Case Else
            ShouldRetry = False
    End Select
End Function

'==============================================================================
' 응답 읽기 도우미
'==============================================================================

' 응답 본문을 UTF-8로 직접 디코딩합니다.
' responseText에 의존하면 Content-Type의 charset 표기에 따라 한글이 깨질 수 있습니다.
Private Function ReadResponseText(ByVal http As Object) As String
    Dim raw As Variant
    Dim b() As Byte

    On Error GoTo Fallback
    raw = http.responseBody
    If IsEmpty(raw) Or IsNull(raw) Then
        ReadResponseText = ""
        Exit Function
    End If
    b = raw
    ReadResponseText = Utf8ToString(b)
    Exit Function

Fallback:
    On Error Resume Next
    ReadResponseText = http.responseText
End Function

Private Function SafeHeader(ByVal http As Object, ByVal name As String) As String
    On Error Resume Next
    SafeHeader = http.getResponseHeader(name)
    If Err.Number <> 0 Then
        Err.Clear
        SafeHeader = ""
    End If
End Function

Private Function SafeAllHeaders(ByVal http As Object) As String
    On Error Resume Next
    SafeAllHeaders = http.getAllResponseHeaders
    If Err.Number <> 0 Then
        Err.Clear
        SafeAllHeaders = ""
    End If
End Function

' 본문이 JSON 객체/배열이면 파싱해서 돌려주고, 아니면 Nothing.
Private Function TryParseJson(ByVal text As String) As Object
    Dim t As String
    t = Trim$(text)
    If Len(t) = 0 Then Exit Function
    If Left$(t, 1) <> "{" And Left$(t, 1) <> "[" Then Exit Function

    On Error GoTo NotJson
    Dim v As Variant
    Set v = JsonParse(t)
    Set TryParseJson = v
    Exit Function

NotJson:
End Function

' 실패한 응답을 사람이 읽을 수 있는 메시지로 정리합니다.
Public Function PA_DescribeError(ByRef resp As PAResponse) As String
    Dim msg As String

    Select Case resp.StatusCode
        Case 0
            msg = "플로우에 연결하지 못했습니다. 네트워크·프록시·URL을 확인하세요."
        Case 400
            msg = "잘못된 요청입니다. 보낸 JSON이 플로우의 요청 스키마와 맞는지 확인하세요."
        Case 401, 403
            msg = "인증에 실패했습니다. URL의 서명(sig)이 만료되었거나 재생성되었을 수 있습니다."
        Case 404
            msg = "플로우를 찾을 수 없습니다. 플로우가 삭제되었거나 URL이 잘못되었습니다."
        Case 429
            msg = "요청이 너무 많습니다(스로틀링). 잠시 후 다시 시도하세요."
        Case 502, 503, 504
            msg = "Power Automate 측 일시 오류입니다. 잠시 후 다시 시도하세요."
        Case Is >= 500
            msg = "플로우 실행 중 오류가 발생했습니다. 실행 기록을 확인하세요."
        Case Else
            msg = "요청이 실패했습니다."
    End Select

    PA_DescribeError = msg & vbCrLf & _
        "상태 코드: " & resp.StatusCode & vbCrLf & _
        "시도 횟수: " & resp.Attempts & vbCrLf & _
        IIf(Len(resp.RunId) > 0, "실행 ID: " & resp.RunId & vbCrLf, "") & _
        "응답: " & Left$(resp.Body, 1000) & vbCrLf & _
        "상세: " & resp.ErrorMessage
End Function

'==============================================================================
' Excel 범위 <-> JSON
'==============================================================================

' 범위를 JSON 배열(Dictionary의 Collection)로 바꿉니다.
' 첫 행을 머리글로 쓰며, 완전히 빈 행은 건너뜁니다.
'
'   Set rows = PA_RangeToArray(Sheet1.Range("A1").CurrentRegion)
Public Function PA_RangeToArray(ByVal rng As Object, Optional ByVal hasHeader As Boolean = True) As Collection
    Dim result As Collection
    Set result = New Collection

    Dim data As Variant
    data = rng.Value

    If Not IsArray(data) Then                       ' 셀 한 개
        Dim one As Object
        Set one = JsonObject()
        one.Add "value", CellToJsonValue(data)
        result.Add one
        Set PA_RangeToArray = result
        Exit Function
    End If

    Dim rLo As Long, rHi As Long, cLo As Long, cHi As Long
    rLo = LBound(data, 1): rHi = UBound(data, 1)
    cLo = LBound(data, 2): cHi = UBound(data, 2)

    Dim headers() As String
    ReDim headers(cLo To cHi)

    Dim c As Long, r As Long
    If hasHeader Then
        For c = cLo To cHi
            headers(c) = Trim$(CStr(CellToText(data(rLo, c))))
            If Len(headers(c)) = 0 Then headers(c) = "col" & (c - cLo + 1)
        Next c
        rLo = rLo + 1
    Else
        For c = cLo To cHi
            headers(c) = "col" & (c - cLo + 1)
        Next c
    End If

    Dim row As Object
    Dim v As Variant
    Dim isBlank As Boolean

    For r = rLo To rHi
        Set row = JsonObject()
        isBlank = True
        For c = cLo To cHi
            v = CellToJsonValue(data(r, c))
            If Not IsNull(v) Then isBlank = False
            If row.Exists(headers(c)) Then row.Remove headers(c)
            row.Add headers(c), v
        Next c
        If Not isBlank Then result.Add row
    Next r

    Set PA_RangeToArray = result
End Function

' JSON 배열(Dictionary의 Collection)을 시트에 씁니다.
' 열 순서는 첫 항목의 키 순서를 따르고, 뒤에 나오는 새 키는 오른쪽에 추가됩니다.
'
'   PA_ArrayToRange res("data"), Sheet2.Range("A1")
Public Function PA_ArrayToRange(ByVal items As Variant, ByVal topLeft As Object, _
                                Optional ByVal writeHeader As Boolean = True) As Long
    Dim col As Collection

    If TypeName(items) = "Collection" Then
        Set col = items
    Else
        Err.Raise ERR_BASE + 20, "PowerAutomate", _
            "PA_ArrayToRange에는 Collection(JSON 배열)이 필요합니다. 받은 형식: " & TypeName(items)
    End If

    If col.Count = 0 Then
        PA_ArrayToRange = 0
        Exit Function
    End If

    ' 1) 열 목록 수집 (등장 순서 유지)
    Dim colIndex As Object
    Set colIndex = JsonObject()

    Dim i As Long, k As Variant
    For i = 1 To col.Count
        If TypeName(col.Item(i)) = "Dictionary" Then
            For Each k In col.Item(i).Keys
                If Not colIndex.Exists(CStr(k)) Then colIndex.Add CStr(k), colIndex.Count
            Next k
        End If
    Next i

    If colIndex.Count = 0 Then
        Err.Raise ERR_BASE + 21, "PowerAutomate", "배열 안에 객체가 없어 표로 만들 수 없습니다."
    End If

    ' 2) 2차원 배열에 채운 뒤 한 번에 쓰기 (셀 단위 쓰기보다 훨씬 빠름)
    Dim rowCount As Long, colCount As Long, offset As Long
    colCount = colIndex.Count
    offset = IIf(writeHeader, 1, 0)
    rowCount = col.Count + offset

    Dim out() As Variant
    ReDim out(1 To rowCount, 1 To colCount)

    If writeHeader Then
        For Each k In colIndex.Keys
            out(1, colIndex.Item(k) + 1) = k
        Next k
    End If

    Dim d As Object, v As Variant
    For i = 1 To col.Count
        If TypeName(col.Item(i)) = "Dictionary" Then
            Set d = col.Item(i)
            For Each k In d.Keys
                v = FlattenForCell(d.Item(k))
                out(i + offset, colIndex.Item(CStr(k)) + 1) = v
            Next k
        Else
            out(i + offset, 1) = FlattenForCell(col.Item(i))
        End If
    Next i

    topLeft.Resize(rowCount, colCount).Value = out
    PA_ArrayToRange = col.Count
End Function

' 셀 값 -> JSON 값
Private Function CellToJsonValue(ByVal v As Variant) As Variant
    If IsError(v) Then
        CellToJsonValue = Null
        Exit Function
    End If

    Select Case VarType(v)
        Case vbEmpty, vbNull
            CellToJsonValue = Null
        Case vbDate
            ' 시각이 0이면 날짜만, 아니면 날짜+시각 (로컬 시각, 오프셋 없음)
            If v = Int(v) Then
                CellToJsonValue = Format$(v, "yyyy-mm-dd")
            Else
                CellToJsonValue = Format$(v, "yyyy-mm-dd") & "T" & Format$(v, "hh:nn:ss")
            End If
        Case vbString
            If Len(v) = 0 Then
                CellToJsonValue = Null
            Else
                CellToJsonValue = CStr(v)
            End If
        Case vbBoolean
            CellToJsonValue = CBool(v)
        Case Else
            CellToJsonValue = v
    End Select
End Function

Private Function CellToText(ByVal v As Variant) As String
    If IsError(v) Then
        CellToText = ""
    ElseIf IsNull(v) Or IsEmpty(v) Then
        CellToText = ""
    Else
        CellToText = CStr(v)
    End If
End Function

' 중첩 객체/배열은 셀에 넣을 수 없으므로 JSON 문자열로 접습니다.
Private Function FlattenForCell(ByVal v As Variant) As Variant
    If IsObject(v) Then
        If v Is Nothing Then
            FlattenForCell = ""
        Else
            FlattenForCell = JsonStringify(v)
        End If
    ElseIf IsNull(v) Then
        FlattenForCell = ""
    ElseIf VarType(v) = vbBoolean Then
        FlattenForCell = CBool(v)
    Else
        FlattenForCell = v
    End If
End Function

'==============================================================================
' 기타 도우미
'==============================================================================

' 중복 처리 방지용 요청 ID. 플로우 쪽에서 이 값을 기록해 두고
' 같은 ID가 다시 오면 건너뛰면 재시도로 인한 중복 실행을 막을 수 있습니다.
Public Function PA_NewRequestId() As String
    Static seq As Long
    Static seeded As Boolean

    If Not seeded Then
        Randomize
        seeded = True
    End If

    seq = seq + 1
    PA_NewRequestId = Format$(Now, "yyyymmddhhnnss") & "-" & _
                      Right$("0000" & Hex$(Int(Rnd() * 65536)), 4) & "-" & _
                      Right$("000" & CStr(seq), 4)
End Function

' UI를 멈추지 않고 지정한 밀리초만큼 기다립니다.
Private Sub WaitMs(ByVal ms As Long)
    Dim i As Long, steps As Long
    If ms <= 0 Then Exit Sub
    steps = ms \ 50
    For i = 1 To steps
        apiSleep 50
        DoEvents
    Next i
    If (ms Mod 50) > 0 Then apiSleep ms Mod 50
End Sub

'==============================================================================
' UTF-8 인코딩 / 디코딩 (외부 참조 없음)
'==============================================================================

' VBA String(UTF-16) -> UTF-8 바이트 배열
Private Function Utf8Bytes(ByVal s As String) As Byte()
    Dim n As Long
    n = Len(s)
    If n = 0 Then
        Dim none(0 To 0) As Byte
        Utf8Bytes = none                      ' 호출부에서 Len(body)>0 을 먼저 확인합니다
        Exit Function
    End If

    Dim buf() As Byte
    ReDim buf(0 To n * 3 + 4)                 ' UTF-16 단위당 최대 3바이트

    Dim p As Long, i As Long
    Dim c As Long, c2 As Long, cp As Long

    i = 1
    Do While i <= n
        c = AscW(Mid$(s, i, 1))
        If c < 0 Then c = c + 65536

        ' 서로게이트 쌍이면 하나의 코드 포인트로 합칩니다.
        cp = c
        If c >= &HD800& And c <= &HDBFF& And i < n Then
            c2 = AscW(Mid$(s, i + 1, 1))
            If c2 < 0 Then c2 = c2 + 65536
            If c2 >= &HDC00& And c2 <= &HDFFF& Then
                cp = &H10000 + ((c - &HD800&) * &H400&) + (c2 - &HDC00&)
                i = i + 1
            End If
        End If

        If cp < &H80& Then
            buf(p) = CByte(cp): p = p + 1
        ElseIf cp < &H800& Then
            buf(p) = CByte(&HC0& Or (cp \ &H40&)): p = p + 1
            buf(p) = CByte(&H80& Or (cp And &H3F&)): p = p + 1
        ElseIf cp < &H10000 Then
            buf(p) = CByte(&HE0& Or (cp \ &H1000&)): p = p + 1
            buf(p) = CByte(&H80& Or ((cp \ &H40&) And &H3F&)): p = p + 1
            buf(p) = CByte(&H80& Or (cp And &H3F&)): p = p + 1
        Else
            buf(p) = CByte(&HF0& Or (cp \ &H40000)): p = p + 1
            buf(p) = CByte(&H80& Or ((cp \ &H1000&) And &H3F&)): p = p + 1
            buf(p) = CByte(&H80& Or ((cp \ &H40&) And &H3F&)): p = p + 1
            buf(p) = CByte(&H80& Or (cp And &H3F&)): p = p + 1
        End If

        i = i + 1
    Loop

    ReDim Preserve buf(0 To p - 1)
    Utf8Bytes = buf
End Function

' UTF-8 바이트 배열 -> VBA String(UTF-16)
Private Function Utf8ToString(ByRef b() As Byte) As String
    Dim lo As Long, hi As Long

    On Error GoTo EmptyInput
    lo = LBound(b)
    hi = UBound(b)
    On Error GoTo 0
    If hi < lo Then Exit Function

    Dim res As String
    res = Space$(hi - lo + 1)                 ' 디코딩 결과는 바이트 수를 넘지 않습니다

    Dim outPos As Long, i As Long, k As Long
    Dim c As Long, cp As Long, extra As Long

    i = lo
    Do While i <= hi
        c = b(i)

        If c < &H80& Then
            cp = c: extra = 0
        ElseIf (c And &HE0&) = &HC0& Then
            cp = c And &H1F&: extra = 1
        ElseIf (c And &HF0&) = &HE0& Then
            cp = c And &HF&: extra = 2
        ElseIf (c And &HF8&) = &HF0& Then
            cp = c And &H7&: extra = 3
        Else
            cp = &HFFFD&: extra = 0           ' 잘못된 선두 바이트
        End If

        For k = 1 To extra
            i = i + 1
            If i > hi Then
                cp = &HFFFD&
                Exit For
            End If
            cp = (cp * &H40&) Or (b(i) And &H3F&)
        Next k

        If cp > &H10FFFF Or cp < 0 Then cp = &HFFFD&

        If cp < &H10000 Then
            outPos = outPos + 1
            Mid$(res, outPos, 1) = ChrW$(cp)
        Else
            cp = cp - &H10000
            outPos = outPos + 1
            Mid$(res, outPos, 1) = ChrW$(&HD800& + (cp \ &H400&))
            outPos = outPos + 1
            Mid$(res, outPos, 1) = ChrW$(&HDC00& + (cp And &H3FF&))
        End If

        i = i + 1
    Loop

    Utf8ToString = Left$(res, outPos)
    Exit Function

EmptyInput:
    Utf8ToString = ""
End Function
