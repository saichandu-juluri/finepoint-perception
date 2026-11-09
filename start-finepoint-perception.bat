@echo off
rem FinePoint Perception: starts the app and opens it in the browser. Double-click this file, or click the FinePoint
rem Perception shortcut that install.ps1 makes in the Start menu and on the desktop: the shortcut runs this file with no
rem window (start-finepoint-perception.pyw), with its output in %LOCALAPPDATA%\FinePoint Perception\FinePoint Perception.log.
rem Uploads that a restart of the computer cut off carry on as soon as it runs.
setlocal
cd /d "%~dp0" || exit /b 1
set "PATH=%USERPROFILE%\.pixi\bin;%PATH%"
set URL=http://127.0.0.1:3210
curl -fs -o NUL %URL% && (start "" %URL% & exit /b 0)
if not exist viewer\.next\BUILD_ID (
  pixi run --frozen -e core viewer-build || goto failed
)
rem The app goes on in this console after this file ends: in its window, or in none when the shortcut started it.
start "" /b pixi run --frozen -e core viewer
rem timeout needs a keyboard, which the shortcut's run has not: ping waits the second
for /l %%i in (1,1,150) do (
  curl -fs -o NUL %URL% && goto open
  ping -n 2 127.0.0.1 >NUL
)
:failed
echo FinePoint Perception did not start. Paste the install line into PowerShell again, or send the FinePoint team the lines above.
pause
exit /b 1
:open
start "" %URL%
