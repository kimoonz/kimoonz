Attribute VB_Name = "ExcelToPowerPoint"
'==============================================================================
' ExcelToPowerPoint - 엑셀 차트/표를 PowerPoint 슬라이드로 자동 반영
'------------------------------------------------------------------------------
' 참조 추가 불필요(후기 바인딩). PowerPoint가 설치되어 있어야 합니다.
' 32비트 · 64비트 Office 모두 지원.
'
' 공개 프로시저
'   XP_ExportAllCharts        통합 문서의 모든 차트를 슬라이드 1장씩으로 내보내기
'   XP_ExportCharts           지정한 시트들의 차트만 내보내기
'   XP_AddChartSlide          열린 프레젠테이션에 차트 1개를 슬라이드로 추가
'   XP_AddRangeSlide          범위(표)를 그림으로 슬라이드에 추가
'   XP_RefreshLinkedPictures  기존 PPT에서 이름표(태그)가 달린 그림을 최신 차트로 교체
'
' 동작 방식
'   차트를 임시 PNG로 Export 한 뒤 AddPicture 로 넣습니다.
'   클립보드 복사/붙여넣기보다 훨씬 안정적입니다(다른 프로그램 간섭 없음).
'==============================================================================
Option Explicit

Private Const ERR_BASE As Long = vbObjectError + 3000

' PowerPoint 상수 (후기 바인딩이라 직접 정의)
Private Const ppLayoutBlank As Long = 12
Private Const ppLayoutTitleOnly As Long = 11
Private Const msoTrue As Long = -1
Private Const msoTextOrientationHorizontal As Long = 1

' 슬라이드 여백(포인트). 슬라이드 크기에서 이만큼 뺀 영역에 그림을 맞춥니다.
Private Const MARGIN_TOP As Single = 80      ' 제목 자리
Private Const MARGIN_SIDE As Single = 40
Private Const MARGIN_BOTTOM As Single = 40

' 내보내는 그림의 해상도 배율(2 = 가로세로 2배 픽셀, 선명한 화질)
Private Const EXPORT_SCALE As Single = 2

'==============================================================================
' 최상위 API
'==============================================================================

' 통합 문서의 모든 차트(차트 개체 + 차트 시트)를 새 프레젠테이션으로 내보냅니다.
'   XP_ExportAllCharts                                    ' 새 PPT, 저장 안 함
'   XP_ExportAllCharts savePath:="C:\보고\주간보고.pptx"
'   XP_ExportAllCharts templatePath:="C:\보고\회사템플릿.potx", savePath:="C:\보고\주간보고.pptx"
Public Sub XP_ExportAllCharts(Optional ByVal templatePath As String = "", _
                              Optional ByVal savePath As String = "", _
                              Optional ByVal closeAfterSave As Boolean = False)
    Dim sheetNames() As String
    Dim ws As Object, i As Long

    ReDim sheetNames(0 To ThisWorkbook.Worksheets.Count - 1)
    For Each ws In ThisWorkbook.Worksheets
        sheetNames(i) = ws.Name
        i = i + 1
    Next ws

    XP_ExportCharts sheetNames, templatePath, savePath, closeAfterSave
End Sub

' 지정한 시트의 차트만 내보냅니다.
'   XP_ExportCharts Array("매출", "재고"), savePath:="C:\보고\주간보고.pptx"
Public Sub XP_ExportCharts(ByVal sheetNames As Variant, _
                           Optional ByVal templatePath As String = "", _
                           Optional ByVal savePath As String = "", _
                           Optional ByVal closeAfterSave As Boolean = False)
    Dim pptApp As Object, pres As Object
    Dim ws As Object, chObj As Object
    Dim nm As Variant
    Dim exported As Long

    Set pptApp = GetPowerPoint()
    Set pres = NewPresentation(pptApp, templatePath)

    ' 워크시트의 차트 개체
    For Each nm In sheetNames
        Set ws = Nothing
        On Error Resume Next
        Set ws = ThisWorkbook.Worksheets(CStr(nm))
        On Error GoTo 0
        If Not ws Is Nothing Then
            For Each chObj In ws.ChartObjects
                XP_AddChartSlide pres, chObj.Chart, SlideTitleFor(chObj.Chart, ws.Name)
                exported = exported + 1
            Next chObj
        End If
    Next nm

    ' 차트 시트(시트 자체가 차트인 경우)
    Dim chSheet As Object
    For Each chSheet In ThisWorkbook.Charts
        If InArray(sheetNames, chSheet.Name) Then
            XP_AddChartSlide pres, chSheet, SlideTitleFor(chSheet, chSheet.Name)
            exported = exported + 1
        End If
    Next chSheet

    If exported = 0 Then
        pres.Close
        Err.Raise ERR_BASE + 1, "ExcelToPowerPoint", "지정한 시트에서 차트를 찾지 못했습니다."
    End If

    If Len(savePath) > 0 Then
        pres.SaveAs savePath
        If closeAfterSave Then
            pres.Close
        End If
    End If

    pptApp.Visible = msoTrue
End Sub

' 프레젠테이션 끝에 슬라이드를 추가하고 차트를 그림으로 넣습니다.
' cht: Chart 개체 (ChartObject.Chart 또는 차트 시트)
Public Sub XP_AddChartSlide(ByVal pres As Object, ByVal cht As Object, _
                            Optional ByVal slideTitle As String = "")
    Dim sld As Object
    Set sld = pres.Slides.Add(pres.Slides.Count + 1, ppLayoutBlank)

    If Len(slideTitle) > 0 Then AddTitle sld, slideTitle

    Dim pngPath As String
    pngPath = ExportChartPng(cht)

    On Error GoTo CleanFail
    PlacePicture sld, pngPath, TagFor(cht)
    On Error GoTo 0

    DeleteFileSafe pngPath
    Exit Sub

CleanFail:
    Dim n As Long, d As String
    n = Err.Number: d = Err.Description
    DeleteFileSafe pngPath
    Err.Raise n, "ExcelToPowerPoint", d
End Sub

' 범위(표 영역)를 그림으로 슬라이드에 추가합니다.
'   XP_AddRangeSlide pres, Sheet1.Range("A1:F20"), "주간 실적 표"
Public Sub XP_AddRangeSlide(ByVal pres As Object, ByVal rng As Object, _
                            Optional ByVal slideTitle As String = "")
    Dim sld As Object
    Set sld = pres.Slides.Add(pres.Slides.Count + 1, ppLayoutBlank)
    If Len(slideTitle) > 0 Then AddTitle sld, slideTitle

    ' 범위는 Export가 없으므로 임시 차트를 캔버스로 써서 PNG를 만듭니다.
    Dim pngPath As String
    pngPath = ExportRangePng(rng)

    On Error GoTo CleanFail
    PlacePicture sld, pngPath, "RANGE|" & rng.Worksheet.Name & "|" & rng.Address(False, False)
    On Error GoTo 0

    DeleteFileSafe pngPath
    Exit Sub

CleanFail:
    Dim n As Long, d As String
    n = Err.Number: d = Err.Description
    DeleteFileSafe pngPath
    Err.Raise n, "ExcelToPowerPoint", d
End Sub

' 기존 PPT 파일을 열어, 이 모듈이 넣었던 그림(태그로 식별)을 최신 차트 그림으로 교체합니다.
' 매주 같은 보고서 틀에 최신 차트만 갈아 끼울 때 씁니다.
'   XP_RefreshLinkedPictures "C:\보고\주간보고.pptx"
Public Sub XP_RefreshLinkedPictures(ByVal pptPath As String, _
                                    Optional ByVal saveAfter As Boolean = True)
    Dim pptApp As Object, pres As Object
    Dim sld As Object, shp As Object
    Dim tag As String
    Dim refreshed As Long
    Dim i As Long

    Set pptApp = GetPowerPoint()
    Set pres = pptApp.Presentations.Open(pptPath)

    For Each sld In pres.Slides
        ' 교체 중 컬렉션이 바뀌므로 역순으로 순회합니다.
        For i = sld.Shapes.Count To 1 Step -1
            Set shp = sld.Shapes(i)
            tag = ""
            On Error Resume Next
            tag = shp.Tags("XPSOURCE")
            On Error GoTo 0

            If Len(tag) > 0 Then
                If RefreshOneShape(sld, shp, tag) Then refreshed = refreshed + 1
            End If
        Next i
    Next sld

    If saveAfter Then pres.Save
    pptApp.Visible = msoTrue

    If refreshed = 0 Then
        MsgBox "교체할 그림을 찾지 못했습니다." & vbCrLf & _
               "이 모듈(XP_AddChartSlide 등)로 만든 그림에만 원본 태그가 붙습니다.", vbExclamation
    End If
End Sub

'==============================================================================
' 내부: 그림 만들기 / 배치
'==============================================================================

' 차트를 임시 PNG 파일로 내보냅니다. 반환값은 파일 경로.
Private Function ExportChartPng(ByVal cht As Object) As String
    Dim pngPath As String
    pngPath = TempPngPath()

    ' 해상도를 높이려면 잠시 크게 키웠다가 되돌립니다. (차트 시트는 크기 변경 없이 그대로)
    Dim parentObj As Object
    Dim w As Single, h As Single
    Dim resized As Boolean

    On Error Resume Next
    Set parentObj = cht.Parent            ' ChartObject 이면 성공
    If Not parentObj Is Nothing Then
        If TypeName(parentObj) = "ChartObject" Then
            w = parentObj.Width: h = parentObj.Height
            If EXPORT_SCALE > 1 Then
                parentObj.Width = w * EXPORT_SCALE
                parentObj.Height = h * EXPORT_SCALE
                resized = True
            End If
        End If
    End If
    On Error GoTo 0

    Err.Clear
    On Error GoTo RestoreSize
    cht.Export Filename:=pngPath, FilterName:="PNG"
    On Error GoTo 0

RestoreSize:
    Dim n As Long, d As String
    n = Err.Number: d = Err.Description
    If resized Then
        parentObj.Width = w
        parentObj.Height = h
    End If
    If n <> 0 Then Err.Raise n, "ExcelToPowerPoint", "차트 PNG 내보내기 실패: " & d

    ExportChartPng = pngPath
End Function

' 범위를 PNG로 만듭니다. 범위 그림을 임시 차트에 붙여 Export 하는 표준 기법입니다.
Private Function ExportRangePng(ByVal rng As Object) As String
    Dim pngPath As String
    pngPath = TempPngPath()

    Dim ws As Object
    Set ws = rng.Worksheet

    rng.CopyPicture Appearance:=1, Format:=-4147          ' xlScreen, xlPicture

    Dim tmpCh As Object
    Set tmpCh = ws.ChartObjects.Add(rng.Left, rng.Top, rng.Width * EXPORT_SCALE, rng.Height * EXPORT_SCALE)

    On Error GoTo CleanFail
    tmpCh.Chart.Paste
    ' 붙인 그림을 차트 크기에 맞춥니다.
    On Error Resume Next
    With tmpCh.Chart.Shapes(1)
        .Left = 0: .Top = 0
        .Width = tmpCh.Width
        .Height = tmpCh.Height
    End With
    On Error GoTo CleanFail

    tmpCh.Chart.Export Filename:=pngPath, FilterName:="PNG"
    tmpCh.Delete
    Application.CutCopyMode = False

    ExportRangePng = pngPath
    Exit Function

CleanFail:
    Dim n As Long, d As String
    n = Err.Number: d = Err.Description
    On Error Resume Next
    tmpCh.Delete
    Application.CutCopyMode = False
    Err.Raise n, "ExcelToPowerPoint", "범위 PNG 만들기 실패: " & d
End Function

' PNG를 슬라이드 중앙(제목 아래)에 비율 유지로 배치하고 원본 태그를 붙입니다.
Private Sub PlacePicture(ByVal sld As Object, ByVal pngPath As String, ByVal sourceTag As String)
    Dim pres As Object
    Set pres = sld.Parent

    Dim slideW As Single, slideH As Single
    slideW = pres.PageSetup.SlideWidth
    slideH = pres.PageSetup.SlideHeight

    Dim pic As Object
    ' 원본 크기로 넣은 뒤 영역에 맞춰 줄입니다. (-1 = msoTrue 링크 없음/저장 포함)
    Set pic = sld.Shapes.AddPicture(pngPath, 0, msoTrue, 0, 0, -1, -1)

    Dim maxW As Single, maxH As Single, scaleF As Single
    maxW = slideW - MARGIN_SIDE * 2
    maxH = slideH - MARGIN_TOP - MARGIN_BOTTOM

    scaleF = maxW / pic.Width
    If maxH / pic.Height < scaleF Then scaleF = maxH / pic.Height
    If scaleF > 1 Then scaleF = 1        ' 확대는 하지 않음(화질 저하)

    pic.LockAspectRatio = msoTrue
    pic.Width = pic.Width * scaleF
    pic.Left = (slideW - pic.Width) / 2
    pic.Top = MARGIN_TOP + (maxH - pic.Height) / 2

    pic.Tags.Add "XPSOURCE", sourceTag
End Sub

' 제목 텍스트 상자를 추가합니다.
Private Sub AddTitle(ByVal sld As Object, ByVal titleText As String)
    Dim pres As Object
    Set pres = sld.Parent

    Dim tb As Object
    Set tb = sld.Shapes.AddTextbox(msoTextOrientationHorizontal, _
                                   MARGIN_SIDE, 20, _
                                   pres.PageSetup.SlideWidth - MARGIN_SIDE * 2, 44)
    With tb.TextFrame.TextRange
        .Text = titleText
        .Font.Size = 24
        .Font.Bold = msoTrue
    End With
End Sub

' 태그를 보고 원본 차트/범위를 다시 그림으로 만들어 교체합니다.
Private Function RefreshOneShape(ByVal sld As Object, ByVal shp As Object, ByVal tag As String) As Boolean
    Dim parts() As String
    parts = Split(tag, "|")
    If UBound(parts) < 2 Then Exit Function

    Dim pngPath As String
    Dim ws As Object

    On Error GoTo Fail
    Select Case parts(0)
        Case "CHART"
            Set ws = ThisWorkbook.Worksheets(parts(1))
            pngPath = ExportChartPng(ws.ChartObjects(parts(2)).Chart)
        Case "CHARTSHEET"
            pngPath = ExportChartPng(ThisWorkbook.Charts(parts(1)))
        Case "RANGE"
            Set ws = ThisWorkbook.Worksheets(parts(1))
            pngPath = ExportRangePng(ws.Range(parts(2)))
        Case Else
            Exit Function
    End Select

    ' 위치·크기를 보존한 채 교체
    Dim l As Single, t As Single, w As Single, h As Single
    l = shp.Left: t = shp.Top: w = shp.Width: h = shp.Height
    shp.Delete

    Dim pic As Object
    Set pic = sld.Shapes.AddPicture(pngPath, 0, msoTrue, l, t, w, h)
    pic.Tags.Add "XPSOURCE", tag

    DeleteFileSafe pngPath
    RefreshOneShape = True
    Exit Function

Fail:
    DeleteFileSafe pngPath
    ' 원본을 못 찾는 그림은 건너뜁니다(삭제된 차트 등)
End Function

'==============================================================================
' 내부: PowerPoint / 파일 도우미
'==============================================================================

Private Function GetPowerPoint() As Object
    Dim app As Object
    On Error Resume Next
    Set app = GetObject(, "PowerPoint.Application")     ' 이미 실행 중이면 재사용
    If app Is Nothing Then Set app = CreateObject("PowerPoint.Application")
    On Error GoTo 0

    If app Is Nothing Then
        Err.Raise ERR_BASE + 2, "ExcelToPowerPoint", _
            "PowerPoint를 실행할 수 없습니다. 설치되어 있는지 확인하세요."
    End If

    app.Visible = msoTrue
    Set GetPowerPoint = app
End Function

Private Function NewPresentation(ByVal pptApp As Object, ByVal templatePath As String) As Object
    If Len(templatePath) > 0 Then
        If Len(Dir(templatePath)) = 0 Then
            Err.Raise ERR_BASE + 3, "ExcelToPowerPoint", "템플릿 파일이 없습니다: " & templatePath
        End If
        ' 템플릿(potx)을 Untitled:=msoTrue 로 열면 사본(새 프레젠테이션)이 만들어집니다.
        Set NewPresentation = pptApp.Presentations.Open(templatePath, , msoTrue, msoTrue)
    Else
        Set NewPresentation = pptApp.Presentations.Add
    End If
End Function

' 차트의 원본 위치를 담는 태그 문자열
Private Function TagFor(ByVal cht As Object) As String
    On Error Resume Next
    If TypeName(cht.Parent) = "ChartObject" Then
        TagFor = "CHART|" & cht.Parent.Parent.Name & "|" & cht.Parent.Name
    Else
        TagFor = "CHARTSHEET|" & cht.Name & "|-"
    End If
End Function

Private Function SlideTitleFor(ByVal cht As Object, ByVal fallback As String) As String
    On Error Resume Next
    If cht.HasTitle Then SlideTitleFor = cht.ChartTitle.Text
    If Len(SlideTitleFor) = 0 Then SlideTitleFor = fallback
End Function

Private Function TempPngPath() As String
    TempPngPath = Environ$("TEMP") & "\xp_" & Format$(Now, "yyyymmddhhnnss") & "_" & _
                  CLng(Timer * 100) & ".png"
End Function

Private Sub DeleteFileSafe(ByVal path As String)
    On Error Resume Next
    If Len(path) > 0 Then
        If Len(Dir(path)) > 0 Then Kill path
    End If
End Sub

Private Function InArray(ByVal arr As Variant, ByVal value As String) As Boolean
    Dim v As Variant
    For Each v In arr
        If StrComp(CStr(v), value, vbTextCompare) = 0 Then
            InArray = True
            Exit Function
        End If
    Next v
End Function
