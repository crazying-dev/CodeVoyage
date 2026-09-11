' CodeVoyage 后台启动脚本（Windows）
' 作用：无控制台窗口启动常驻进程；启动后浏览器页面只是控制台，关闭页面不会停止运行。
' 停止方式：系统托盘「退出」（或结束后台进程）。
Option Explicit

Dim fso, shell, base
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

base = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = base

' 优先使用 pythonw（无窗口）；找不到时回退 python
Dim pyCmd
pyCmd = "pythonw"
If Not FileExistsOnPath("pythonw.exe") Then
    pyCmd = "python"
End If

shell.Run pyCmd & " main.py", 0, False


Function FileExistsOnPath(exeName)
    Dim paths, i, p
    paths = Split(shell.ExpandEnvironmentStrings("%PATH%"), ";")
    For i = 0 To UBound(paths)
        If Len(paths(i)) > 0 Then
            If fso.FileExists(fso.BuildPath(paths(i), exeName)) Then
                FileExistsOnPath = True
                Exit Function
            End If
        End If
    Next
    FileExistsOnPath = False
End Function
