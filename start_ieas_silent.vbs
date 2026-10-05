Set objShell = CreateObject("WScript.Shell")
Set objFSO = CreateObject("Scripting.FileSystemObject")

' Change to project directory
Dim projectDir
projectDir = "c:\Users\N.Sreekanth\OneDrive\Desktop\Intelligent Sytem\candidate_solution"

' Kill any old server on port 8001 silently
objShell.Run "cmd /c for /f ""tokens=5"" %a in ('netstat -aon ^| findstr "":8001""') do taskkill /PID %a /F", 0, True

' Start uvicorn server silently in background (Window = 0 means hidden)
objShell.Run "cmd /c cd /d """ & projectDir & """ && python -m uvicorn backend.main:app --host 127.0.0.1 --port 8001", 0, False

' Wait 4 seconds for server to come up
WScript.Sleep 4000

' Open browser
objShell.Run "http://127.0.0.1:8001/app/login.html"
