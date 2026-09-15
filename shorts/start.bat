@echo off
setlocal
chcp 65001 >nul 2>nul
cd /d "%~dp0"

echo.
echo   Shorts Renderer
echo   ---------------
echo.

rem --- ffmpeg 확인 ---
where ffmpeg >nul 2>nul
if not "%ERRORLEVEL%"=="0" (
  echo   [!] ffmpeg 가 없습니다 / ffmpeg not found
  echo       winget install Gyan.FFmpeg
  echo       설치 후 이 창을 닫고 다시 실행하세요.
  echo.
  pause
  exit /b 1
)

rem --- Python 실행기 찾기 ---
set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo   [!] Python 이 없습니다 / Python not found
  echo       winget install Python.Python.3.12
  echo.
  pause
  exit /b 1
)

rem --- 가상환경 준비 ---
if not exist ".venv\Scripts\python.exe" (
  echo   가상환경을 만드는 중... / creating venv
  %PY% -m venv .venv
  if not exist ".venv\Scripts\python.exe" (
    echo   [!] 가상환경 생성 실패 / venv creation failed
    echo.
    pause
    exit /b 1
  )
)

rem --- 라이브러리 설치 (venv 의 python 을 직접 호출) ---
echo   필요한 라이브러리 확인 중... / checking packages
".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.txt
if not "%ERRORLEVEL%"=="0" (
  echo   [!] 라이브러리 설치 실패 / package install failed
  echo.
  pause
  exit /b 1
)

echo.
".venv\Scripts\python.exe" app.py %*
echo.
pause
