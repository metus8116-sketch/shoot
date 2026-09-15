#!/bin/bash
# macOS: 파인더에서 더블클릭하면 실행됩니다.
cd "$(dirname "$0")" || exit 1

echo
echo "  숏폼 렌더러를 시작합니다..."
echo

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "  [오류] ffmpeg 가 설치되어 있지 않습니다."
  echo "         터미널에서 실행하세요:  brew install ffmpeg"
  echo
  read -r -p "엔터를 누르면 닫힙니다."
  exit 1
fi

if [ ! -d .venv ]; then
  echo "  가상환경을 만드는 중..."
  python3 -m venv .venv || { echo "  [오류] python3 를 찾을 수 없습니다."; read -r; exit 1; }
fi

# shellcheck disable=SC1091
source .venv/bin/activate
echo "  필요한 라이브러리를 확인하는 중..."
python -m pip install --quiet --disable-pip-version-check -r requirements.txt || {
  echo "  [오류] 라이브러리 설치 실패"; read -r; exit 1; }

echo
python app.py
