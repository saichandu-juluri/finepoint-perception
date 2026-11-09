@echo off
rem FinePoint Perception on this Windows computer: double-click to start the app and open it in the browser. Uploads that a
rem restart cut off carry on as soon as the app runs. Uploads need one sign-in in the app's Upload panel.
cd /d "%~dp0"
curl -fs -o NUL http://127.0.0.1:3210 && (start "" http://127.0.0.1:3210 & exit /b 0)
if not exist viewer\.next\BUILD_ID (pixi run --frozen viewer-build || (echo FinePoint Perception: the app could not be built & pause & exit /b 1))
start "FinePoint Perception" /min pixi run --frozen -e core viewer
for /l %%i in (1,1,150) do (curl -fs -o NUL http://127.0.0.1:3210 && goto open) & timeout /t 1 /nobreak >NUL
:open
start "" http://127.0.0.1:3210
