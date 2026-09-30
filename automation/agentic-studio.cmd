@echo off
setlocal
cd /d "%~dp0"
set "STUDIO_PYTHON=%LENSES_AGENT_PYTHON%"
if "%STUDIO_PYTHON%"=="" set "STUDIO_PYTHON=%~dp0data\blender_agent\venv\Scripts\python.exe"
if not exist "%STUDIO_PYTHON%" (
  echo Create the Blender agent environment first. See blender_agent\STUDIO.md.
  pause
  exit /b 1
)
"%STUDIO_PYTHON%" -m blender_agent.studio_server --start-ar --open %*
if errorlevel 1 pause
endlocal
