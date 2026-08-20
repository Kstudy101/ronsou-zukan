@echo off
rem 윈도우 작업 스케줄러가 1시간마다 부른다. 로그는 autorun.log 에 쌓인다.
rem 한 번에 한 편만 처리하고, 일일 한도에 닿으면 아무것도 하지 않고 끝난다.
cd /d "%~dp0"
"%~dp0..\.venv\Scripts\python.exe" "%~dp0autorun.py"
exit /b %ERRORLEVEL%
