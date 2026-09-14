@echo off
rem ============================================================================
rem  PawPet launcher.
rem
rem  NOTE: this file is intentionally ASCII-only.
rem  cmd.exe parses .cmd files using the OEM codepage (936/GBK on Chinese
rem  Windows). A UTF-8 encoded Chinese character can end with a byte that GBK
rem  treats as a lead byte, which then swallows the following newline and merges
rem  two lines into one broken command. Keeping the launcher ASCII-only makes it
rem  work on every Windows codepage.
rem
rem  Why this file matters: the old launcher called the global pythonw, which
rem  bypassed the project's .venv where PySide6 and the vision deps live. That is
rem  why the app used to report "dependencies not installed" even though they
rem  were. This launcher prefers .venv and falls back loudly when anything fails.
rem ============================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "VENV_PY=%~dp0.venv\Scripts\python.exe"
set "VENV_PYW=%~dp0.venv\Scripts\pythonw.exe"
set "ENTRY=%~dp0run_pawpet.py"

if not exist "%ENTRY%" (
    echo [PawPet] Cannot find "%ENTRY%".
    pause
    exit /b 1
)

rem Prefer the venv's windowless interpreter.
if exist "%VENV_PYW%" (
    set "PYW=%VENV_PYW%"
    set "PY=%VENV_PY%"
) else (
    for /f "delims=" %%I in ('where pythonw 2^>nul') do (
        if not defined PYW set "PYW=%%I"
    )
    for /f "delims=" %%I in ('where python 2^>nul') do (
        if not defined PY set "PY=%%I"
    )
)

if not defined PYW (
    echo [PawPet] Python was not found.
    echo          Install Python 3.10+ and tick "Add python.exe to PATH".
    pause
    exit /b 1
)

rem Launch windowless. A non-zero exit means the interpreter failed right away,
rem in which case re-run with the console interpreter so the error is visible.
rem The redirections keep the launcher from leaving the GUI process holding the
rem parent's stdout/stderr handles (which would keep a calling script's pipe
rem open for as long as PawPet runs).
start "" /b "%PYW%" "%ENTRY%" <nul >nul 2>&1
if not errorlevel 1 exit /b 0

:failed
echo.
echo [PawPet] Startup failed. Details below:
echo ---------------------------------------------------------------
if defined PY (
    "%PY%" "%ENTRY%"
) else (
    python "%ENTRY%"
)
echo ---------------------------------------------------------------
echo.
echo If it reports a missing PySide6, run:
echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
pause
exit /b 1
