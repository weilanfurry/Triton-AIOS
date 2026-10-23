@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo  Triton AIOS - Preview 0.1.0
echo  ------------------------------------

if not exist ".venv\Scripts\python.exe" (
  echo  [1/3] 创建虚拟环境...
  python -m venv .venv
  if errorlevel 1 (
    echo  创建失败：请确认已安装 Python 3.11+ 并加入 PATH。
    pause
    exit /b 1
  )
)

echo  [2/3] 检查依赖...
".venv\Scripts\python.exe" -c "import fastapi, uvicorn, httpx" >nul 2>nul
if errorlevel 1 (
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo  依赖安装失败，请检查网络后重试。
    pause
    exit /b 1
  )
)

echo  [3/3] 启动桌面： http://127.0.0.1:8000
echo  关闭此窗口即可停止服务。
echo.
start "" "http://127.0.0.1:8000"
".venv\Scripts\python.exe" -m uvicorn desktop_app:app --host 127.0.0.1 --port 8000

pause
