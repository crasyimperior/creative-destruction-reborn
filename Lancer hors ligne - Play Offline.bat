@echo off
rem Creative Destruction Reborn: starts the local server, then the game.
cd /d "%~dp0"
rem Close a previous server still running (two servers would share the ports).
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*offline_server.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
rem Server window starts minimized: do not click inside it during a match.
start "Creative Destruction Reborn server (keep open)" /min cmd /k python "%~dp0reborn\server\offline_server.py"
timeout /t 3 /nobreak >nul
start "" "%~dp0client.exe"
