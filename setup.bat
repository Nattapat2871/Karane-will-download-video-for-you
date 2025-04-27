@echo off
echo =======================================
echo  Setting up Python Virtual Environment
echo =======================================

set VENV_DIR=.venv
set PYTHON_EXE=python

REM ตรวจสอบว่ามี Python ติดตั้งหรือไม่ (ตรวจสอบเบื้องต้น)
where %PYTHON_EXE% >nul 2>nul
if %errorlevel% neq 0 (
    echo Error: Python command '%PYTHON_EXE%' not found in PATH.
    echo Please install Python 3 and ensure it's added to your PATH.
    goto :eof
)

REM ตรวจสอบว่ามี requirements.txt หรือไม่
if not exist requirements.txt (
    echo Error: requirements.txt not found in the current directory.
    goto :eof
)

REM ตรวจสอบว่าโฟลเดอร์ .venv มีอยู่แล้วหรือยัง
if exist %VENV_DIR% (
    echo Virtual environment '%VENV_DIR%' already exists. Skipping creation.
) else (
    echo Creating virtual environment in '%VENV_DIR%'...
    %PYTHON_EXE% -m venv %VENV_DIR%
    if %errorlevel% neq 0 (
        echo Error: Failed to create virtual environment.
        goto :eof
    )
    echo Virtual environment created successfully.
)

echo.
echo =======================================
echo  Installing requirements...
echo =======================================
echo Using pip from: %VENV_DIR%\Scripts\pip.exe

REM ติดตั้ง Dependencies จาก requirements.txt โดยเรียก pip จากใน venv โดยตรง
%VENV_DIR%\Scripts\python.exe -m pip install -r requirements.txt

if %errorlevel% neq 0 (
    echo Error: Failed to install requirements from requirements.txt.
    echo Please check the file and your internet connection.
) else (
    echo Requirements installed successfully.
    echo.
    echo Setup complete! You can now run the application (e.g., using run.bat).
)

echo =======================================
echo.
pause
:eof