@echo off
setlocal
set "APPDIR=%~dp0"
set "TARGET=%APPDIR%Faithful Remaster.exe"
set "SHORTCUT=%USERPROFILE%\Desktop\Faithful Remaster.lnk"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%SHORTCUT%'); $s.TargetPath = '%TARGET%'; $s.WorkingDirectory = '%APPDIR%'; $s.IconLocation = '%APPDIR%assets\faithful_remaster.ico,0'; $s.Description = 'Faithful Remaster'; $s.Save()"
if errorlevel 1 (
  echo Could not create the desktop shortcut.
  pause
  exit /b 1
)
echo Created: %SHORTCUT%
pause
