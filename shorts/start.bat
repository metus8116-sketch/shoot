@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo   숏폼 렌더러를 시작합니다...
echo.

where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo   [오류] ffmpeg 가 설치되어 있지 않습니다.
  echo          PowerShell 에서 다음을 실행한 뒤 창을 다시 여세요:
  echo.
  echo            winget install Gyan.FFmpeg
  echo.
  pause
  exit /b 1
)

if not exist ".venv" (
  echo   가상환경을 만드는 중...
  py -m venv .venv 2>nul || python -m venv .venv
  if errorlevel 1 (
    echo   [오류] Python 을 찾을 수 없습니다. winget install Python.Python.3.12
    pause
    exit /b 1
  )
)

call ".venv\Scripts\activate.bat"
echo   필요한 라이브러리를 확인하는 중...
python -m pip install --quiet --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
  echo   [오류] 라이브러리 설치에 실패했습니다.
  pause
  exit /b 1
)

echo.
python app.py
pause
