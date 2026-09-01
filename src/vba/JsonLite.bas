Attribute VB_Name = "JsonLite"
'==============================================================================
' JsonLite - VBA용 최소 JSON 파서/직렬화기
'------------------------------------------------------------------------------
' 외부 참조가 필요 없습니다. (ScriptControl / VBA-JSON / ADODB 불필요)
' 32비트 · 64비트 Office 모두에서 동작합니다.
'
' 공개 함수
'   JsonParse(text)                  JSON 문자열 -> Dictionary / Collection / 값
'   JsonStringify(value, [pretty])   값 -> JSON 문자열
'   JsonObject()                     빈 Dictionary 생성
'   JsonArray()                      빈 Collection 생성
'   JsonGet(root, path, [default])   "a/b/0/c" 경로로 안전하게 값 꺼내기
'   JsonEscape(s)                    문자열을 JSON 리터럴로 (따옴표 포함)
'
' 매핑 규칙
'   JSON object  <-> Scripting.Dictionary (후기 바인딩, 키는 대소문자 구분)
'   JSON array   <-> Collection (1부터 시작하는 인덱스)
'   JSON string  <-> String
'   JSON number  <-> Long (정수 범위) 또는 Double
'   JSON true/false <-> Boolean
'   JSON null    <-> Null
'==============================================================================
Option Explicit

Private Const ERR_BASE As Long = vbObjectError + 1000

Private mJson As String
Private mPos As Long
Private mLen As Long

'==============================================================================
' 생성 도우미
'==============================================================================

' 빈 JSON 객체(Dictionary)를 만듭니다.
Public Function JsonObject() As Object
    Dim d As Object
    Set d = CreateObject("Scripting.Dictionary")
    d.CompareMode = 0                       ' BinaryCompare: JSON 키는 대소문자를 구분
    Set JsonObject = d
End Function

' 빈 JSON 배열(Collection)을 만듭니다.
Public Function JsonArray() As Collection
    Set JsonArray = New Collection
End Function

'==============================================================================
' 파싱
'==============================================================================

' JSON 문자열을 VBA 값으로 변환합니다. 형식이 잘못되면 오류를 발생시킵니다.
Public Function JsonParse(ByVal text As String) As Variant
    mJson = text
    mLen = Len(text)
    mPos = 1

    SkipWs
    If mPos > mLen Then
        Err.Raise ERR_BASE + 1, "JsonLite", "빈 문자열은 JSON으로 해석할 수 없습니다."
    End If

    Dim v As Variant
    AssignAny v, ParseValue()

    SkipWs
    If mPos <= mLen Then
        Err.Raise ERR_BASE + 2, "JsonLite", _
            "JSON이 끝난 뒤에 불필요한 문자가 있습니다. 위치=" & mPos & ", 문자='" & Mid$(mJson, mPos, 1) & "'"
    End If

    If IsObject(v) Then Set JsonParse = v Else JsonParse = v
End Function

Private Function ParseValue() As Variant
    SkipWs
    If mPos > mLen Then
        Err.Raise ERR_BASE + 3, "JsonLite", "값이 있어야 할 자리에서 문자열이 끝났습니다."
    End If

    Select Case Mid$(mJson, mPos, 1)
        Case "{":  Set ParseValue = ParseObject()
        Case "[":  Set ParseValue = ParseArray()
        Case """": ParseValue = ParseString()
        Case "t":  ParseValue = ParseKeyword("true", True)
        Case "f":  ParseValue = ParseKeyword("false", False)
        Case "n":  ParseValue = ParseKeyword("null", Null)
        Case Else: ParseValue = ParseNumber()
    End Select
End Function

Private Function ParseObject() As Object
    Dim d As Object
    Set d = JsonObject()

    mPos = mPos + 1                          ' '{' 소비
    SkipWs
    If mPos <= mLen Then
        If Mid$(mJson, mPos, 1) = "}" Then
            mPos = mPos + 1
            Set ParseObject = d
            Exit Function
        End If
    End If

    Dim k As String, c As String
    Dim v As Variant
    Do
        SkipWs
        If mPos > mLen Then Err.Raise ERR_BASE + 4, "JsonLite", "객체가 닫히지 않았습니다."
        If Mid$(mJson, mPos, 1) <> """" Then
            Err.Raise ERR_BASE + 5, "JsonLite", "객체의 키는 큰따옴표 문자열이어야 합니다. 위치=" & mPos
        End If

        k = ParseString()

        SkipWs
        If mPos > mLen Then Err.Raise ERR_BASE + 6, "JsonLite", "':' 앞에서 문자열이 끝났습니다."
        If Mid$(mJson, mPos, 1) <> ":" Then
            Err.Raise ERR_BASE + 6, "JsonLite", "키 뒤에는 ':' 가 와야 합니다. 위치=" & mPos
        End If
        mPos = mPos + 1

        AssignAny v, ParseValue()
        If d.Exists(k) Then d.Remove k       ' 중복 키는 마지막 값이 이깁니다(JSON 관행)
        d.Add k, v

        SkipWs
        If mPos > mLen Then Err.Raise ERR_BASE + 7, "JsonLite", "객체가 닫히지 않았습니다."
        c = Mid$(mJson, mPos, 1)
        If c = "," Then
            mPos = mPos + 1
        ElseIf c = "}" Then
            mPos = mPos + 1
            Exit Do
        Else
            Err.Raise ERR_BASE + 8, "JsonLite", "',' 또는 '}' 가 와야 합니다. 위치=" & mPos & ", 문자='" & c & "'"
        End If
    Loop

    Set ParseObject = d
End Function

Private Function ParseArray() As Collection
    Dim col As Collection
    Set col = New Collection

    mPos = mPos + 1                          ' '[' 소비
    SkipWs
    If mPos <= mLen Then
        If Mid$(mJson, mPos, 1) = "]" Then
            mPos = mPos + 1
            Set ParseArray = col
            Exit Function
        End If
    End If

    Dim v As Variant, c As String
    Do
        AssignAny v, ParseValue()
        col.Add v

        SkipWs
        If mPos > mLen Then Err.Raise ERR_BASE + 9, "JsonLite", "배열이 닫히지 않았습니다."
        c = Mid$(mJson, mPos, 1)
        If c = "," Then
            mPos = mPos + 1
        ElseIf c = "]" Then
            mPos = mPos + 1
            Exit Do
        Else
            Err.Raise ERR_BASE + 10, "JsonLite", "',' 또는 ']' 가 와야 합니다. 위치=" & mPos & ", 문자='" & c & "'"
        End If
    Loop

    Set ParseArray = col
End Function

Private Function ParseString() As String
    Dim out As String, runStart As Long, c As String, e As String

    mPos = mPos + 1                          ' 여는 따옴표 소비
    Do
        If mPos > mLen Then
            Err.Raise ERR_BASE + 11, "JsonLite", "문자열이 닫히지 않았습니다."
        End If

        ' 이스케이프가 아닌 구간은 한 번에 잘라 붙여 성능을 확보합니다.
        runStart = mPos
        Do While mPos <= mLen
            c = Mid$(mJson, mPos, 1)
            If c = """" Or c = "\" Then Exit Do
            mPos = mPos + 1
        Loop
        If mPos > runStart Then out = out & Mid$(mJson, runStart, mPos - runStart)

        If mPos > mLen Then
            Err.Raise ERR_BASE + 11, "JsonLite", "문자열이 닫히지 않았습니다."
        End If

        c = Mid$(mJson, mPos, 1)
        If c = """" Then
            mPos = mPos + 1
            Exit Do
        End If

        ' c = "\"
        mPos = mPos + 1
        If mPos > mLen Then Err.Raise ERR_BASE + 12, "JsonLite", "이스케이프 문자가 잘렸습니다."
        e = Mid$(mJson, mPos, 1)
        Select Case e
            Case """": out = out & """"
            Case "\":  out = out & "\"
            Case "/":  out = out & "/"
            Case "b":  out = out & Chr$(8)
            Case "f":  out = out & Chr$(12)
            Case "n":  out = out & vbLf
            Case "r":  out = out & vbCr
            Case "t":  out = out & vbTab
            Case "u"
                If mPos + 4 > mLen Then
                    Err.Raise ERR_BASE + 13, "JsonLite", "\u 이스케이프가 잘렸습니다. 위치=" & mPos
                End If
                ' 끝의 '&' 는 16진 문자열을 Long으로 읽게 하는 VBA 관용구입니다.
                ' (없으면 "&HFFFF" 가 Integer -1 로 해석됩니다)
                out = out & ChrW$(CLng("&H" & Mid$(mJson, mPos + 1, 4) & "&"))
                mPos = mPos + 4
            Case Else
                Err.Raise ERR_BASE + 14, "JsonLite", "알 수 없는 이스케이프입니다: \" & e & " (위치=" & mPos & ")"
        End Select
        mPos = mPos + 1
    Loop

    ParseString = out
End Function

Private Function ParseNumber() As Variant
    Dim startPos As Long, raw As String, c As String
    Dim d As Double

    startPos = mPos
    Do While mPos <= mLen
        c = Mid$(mJson, mPos, 1)
        If InStr("0123456789+-.eE", c) = 0 Then Exit Do
        mPos = mPos + 1
    Loop

    raw = Mid$(mJson, startPos, mPos - startPos)
    If Len(raw) = 0 Then
        Err.Raise ERR_BASE + 15, "JsonLite", _
            "값을 해석할 수 없습니다. 위치=" & startPos & ", 문자='" & Mid$(mJson, startPos, 1) & "'"
    End If

    ' Val() 은 로캘과 무관하게 항상 '.' 를 소수점으로 봅니다. CDbl 은 로캘에 따라 달라집니다.
    d = Val(raw)

    ' 정수 표기이고 Long 범위 안이면 Long으로 돌려줍니다(인덱스·비교에 편리).
    If InStr(raw, ".") = 0 And InStr(1, raw, "e", vbTextCompare) = 0 Then
        If d >= -2147483648# And d <= 2147483647# Then
            ParseNumber = CLng(d)
            Exit Function
        End If
    End If

    ParseNumber = d
End Function

Private Function ParseKeyword(ByVal word As String, ByVal value As Variant) As Variant
    If Mid$(mJson, mPos, Len(word)) <> word Then
        Err.Raise ERR_BASE + 16, "JsonLite", "'" & word & "' 를 기대했습니다. 위치=" & mPos
    End If
    mPos = mPos + Len(word)
    ParseKeyword = value
End Function

Private Sub SkipWs()
    Dim c As String
    Do While mPos <= mLen
        c = Mid$(mJson, mPos, 1)
        If c <> " " And c <> vbTab And c <> vbCr And c <> vbLf Then Exit Do
        mPos = mPos + 1
    Loop
End Sub

'==============================================================================
' 직렬화
'==============================================================================

' 값을 JSON 문자열로 만듭니다. pretty:=True 면 들여쓰기합니다.
Public Function JsonStringify(ByVal value As Variant, Optional ByVal pretty As Boolean = False) As String
    JsonStringify = StringifyValue(value, 0, pretty)
End Function

Private Function StringifyValue(ByRef v As Variant, ByVal depth As Long, ByVal pretty As Boolean) As String
    If IsObject(v) Then
        If v Is Nothing Then
            StringifyValue = "null"
        ElseIf TypeName(v) = "Dictionary" Then
            StringifyValue = StringifyObject(v, depth, pretty)
        ElseIf TypeName(v) = "Collection" Then
            StringifyValue = StringifyCollection(v, depth, pretty)
        Else
            Err.Raise ERR_BASE + 20, "JsonLite", "JSON으로 직렬화할 수 없는 개체입니다: " & TypeName(v)
        End If
        Exit Function
    End If

    If IsArray(v) Then
        StringifyValue = StringifyVbArray(v, depth, pretty)
        Exit Function
    End If

    Select Case VarType(v)
        Case vbNull, vbEmpty
            StringifyValue = "null"
        Case vbBoolean
            StringifyValue = IIf(CBool(v), "true", "false")
        Case vbString
            StringifyValue = JsonEscape(CStr(v))
        Case vbDate
            ' ISO 8601 (로컬 시각, 오프셋 없음). UTC가 필요하면 호출 전에 변환하세요.
            StringifyValue = """" & Format$(v, "yyyy-mm-dd") & "T" & Format$(v, "hh:nn:ss") & """"
        Case vbByte, vbInteger, vbLong
            StringifyValue = CStr(CLng(v))
        Case Else
            ' 그 밖의 수치형(Single/Double/Currency/Decimal/LongLong)
            If IsNumeric(v) Then
                StringifyValue = NumberToJson(v)
            Else
                StringifyValue = JsonEscape(CStr(v))
            End If
    End Select
End Function

Private Function StringifyObject(ByVal d As Object, ByVal depth As Long, ByVal pretty As Boolean) As String
    If d.Count = 0 Then
        StringifyObject = "{}"
        Exit Function
    End If

    Dim parts() As String
    ReDim parts(0 To d.Count - 1)

    Dim k As Variant, i As Long
    Dim v As Variant
    For Each k In d.Keys
        AssignAny v, d.Item(k)
        parts(i) = JsonEscape(CStr(k)) & IIf(pretty, ": ", ":") & StringifyValue(v, depth + 1, pretty)
        i = i + 1
    Next k

    StringifyObject = WrapParts(parts, "{", "}", depth, pretty)
End Function

Private Function StringifyCollection(ByVal col As Collection, ByVal depth As Long, ByVal pretty As Boolean) As String
    If col.Count = 0 Then
        StringifyCollection = "[]"
        Exit Function
    End If

    Dim parts() As String
    ReDim parts(0 To col.Count - 1)

    Dim i As Long
    Dim v As Variant
    For i = 1 To col.Count
        AssignAny v, col.Item(i)
        parts(i - 1) = StringifyValue(v, depth + 1, pretty)
    Next i

    StringifyCollection = WrapParts(parts, "[", "]", depth, pretty)
End Function

Private Function StringifyVbArray(ByRef arr As Variant, ByVal depth As Long, ByVal pretty As Boolean) As String
    Dim lo As Long, hi As Long

    If ArrayDims(arr) > 1 Then
        Err.Raise ERR_BASE + 21, "JsonLite", _
            "다차원 배열은 직렬화할 수 없습니다. Range.Value 처럼 2차원 배열이라면 " & _
            "PowerAutomate.PA_RangeToArray 로 Collection 으로 바꾼 뒤 넘기세요."
    End If

    On Error GoTo EmptyArray
    lo = LBound(arr)
    hi = UBound(arr)
    On Error GoTo 0

    If hi < lo Then
        StringifyVbArray = "[]"
        Exit Function
    End If

    Dim parts() As String
    ReDim parts(0 To hi - lo)

    Dim i As Long
    Dim v As Variant
    For i = lo To hi
        AssignAny v, arr(i)
        parts(i - lo) = StringifyValue(v, depth + 1, pretty)
    Next i

    StringifyVbArray = WrapParts(parts, "[", "]", depth, pretty)
    Exit Function

EmptyArray:
    StringifyVbArray = "[]"
End Function

Private Function WrapParts(ByRef parts() As String, ByVal openCh As String, ByVal closeCh As String, _
                           ByVal depth As Long, ByVal pretty As Boolean) As String
    If Not pretty Then
        WrapParts = openCh & Join(parts, ",") & closeCh
        Exit Function
    End If

    Dim inner As String, outer As String
    inner = String$((depth + 1) * 2, " ")
    outer = String$(depth * 2, " ")
    WrapParts = openCh & vbLf & inner & Join(parts, "," & vbLf & inner) & vbLf & outer & closeCh
End Function

' 문자열을 JSON 리터럴로 만듭니다(양쪽 큰따옴표 포함).
' 비ASCII 문자는 그대로 둡니다 - 전송은 UTF-8로 하므로 한글이 깨지지 않습니다.
Public Function JsonEscape(ByVal s As String) As String
    Dim n As Long
    n = Len(s)
    If n = 0 Then
        JsonEscape = """"""
        Exit Function
    End If

    Dim out As String, runStart As Long, i As Long, code As Long
    out = """"
    runStart = 1

    For i = 1 To n
        code = AscW(Mid$(s, i, 1))
        If code < 0 Then code = code + 65536

        If code = 34 Or code = 92 Or code < 32 Then
            If i > runStart Then out = out & Mid$(s, runStart, i - runStart)
            Select Case code
                Case 34: out = out & "\"""
                Case 92: out = out & "\\"
                Case 8:  out = out & "\b"
                Case 12: out = out & "\f"
                Case 10: out = out & "\n"
                Case 13: out = out & "\r"
                Case 9:  out = out & "\t"
                Case Else: out = out & "\u" & Right$("000" & Hex$(code), 4)
            End Select
            runStart = i + 1
        End If
    Next i

    If n >= runStart Then out = out & Mid$(s, runStart, n - runStart + 1)
    JsonEscape = out & """"
End Function

' Str$ 는 로캘과 무관하게 '.' 를 씁니다. 다만 0.1 을 " .1" 로 만들므로 보정합니다.
Private Function NumberToJson(ByVal n As Variant) As String
    Dim s As String
    s = Trim$(Str$(n))
    If Left$(s, 1) = "." Then
        s = "0" & s
    ElseIf Left$(s, 2) = "-." Then
        s = "-0" & Mid$(s, 2)
    End If
    NumberToJson = s
End Function

'==============================================================================
' 경로 접근
'==============================================================================

' "user/name" 또는 "items/0/id" 형태의 경로로 값을 꺼냅니다.
' 경로 중간이 없으면 오류 대신 defaultValue(기본 Empty)를 돌려줍니다.
' 배열 인덱스는 0부터입니다(JSON 관행). 내부 Collection은 1부터이므로 자동 보정합니다.
Public Function JsonGet(ByVal root As Variant, ByVal path As String, Optional ByVal defaultValue As Variant = Empty) As Variant
    Dim parts() As String
    Dim i As Long, idx As Long
    Dim cur As Variant, key As String

    AssignAny cur, root
    parts = Split(path, "/")

    For i = LBound(parts) To UBound(parts)
        key = parts(i)
        If Len(key) > 0 Then
            If Not IsObject(cur) Then
                If IsObject(defaultValue) Then Set JsonGet = defaultValue Else JsonGet = defaultValue
                Exit Function
            End If

            Select Case TypeName(cur)
                Case "Dictionary"
                    If Not cur.Exists(key) Then
                        If IsObject(defaultValue) Then Set JsonGet = defaultValue Else JsonGet = defaultValue
                        Exit Function
                    End If
                    AssignAny cur, cur.Item(key)

                Case "Collection"
                    If Not IsAllDigits(key) Then
                        If IsObject(defaultValue) Then Set JsonGet = defaultValue Else JsonGet = defaultValue
                        Exit Function
                    End If
                    idx = CLng(key) + 1
                    If idx < 1 Or idx > cur.Count Then
                        If IsObject(defaultValue) Then Set JsonGet = defaultValue Else JsonGet = defaultValue
                        Exit Function
                    End If
                    AssignAny cur, cur.Item(idx)

                Case Else
                    If IsObject(defaultValue) Then Set JsonGet = defaultValue Else JsonGet = defaultValue
                    Exit Function
            End Select
        End If
    Next i

    If IsObject(cur) Then Set JsonGet = cur Else JsonGet = cur
End Function

'==============================================================================
' 내부 도우미
'==============================================================================

' Variant가 개체를 담고 있어도 안전하게 대입합니다.
Private Sub AssignAny(ByRef target As Variant, ByRef value As Variant)
    If IsObject(value) Then
        Set target = value
    Else
        target = value
    End If
End Sub

' 배열의 차원 수를 돌려줍니다. 초기화되지 않은 배열이면 0입니다.
Private Function ArrayDims(ByRef arr As Variant) As Long
    Dim i As Long
    Dim ub As Long
    On Error GoTo Done
    For i = 1 To 60
        ub = UBound(arr, i)
        ArrayDims = i
    Next i
Done:
    On Error GoTo 0
End Function

Private Function IsAllDigits(ByVal s As String) As Boolean
    Dim i As Long, c As Long
    If Len(s) = 0 Then Exit Function
    For i = 1 To Len(s)
        c = Asc(Mid$(s, i, 1))
        If c < 48 Or c > 57 Then Exit Function
    Next i
    IsAllDigits = True
End Function
