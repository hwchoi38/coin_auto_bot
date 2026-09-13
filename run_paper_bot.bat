@echo off

REM 실행 시각과 오류를 logs\scheduled_bot.log 파일에 계속 기록합니다.
echo. >> C:\Users\TEST\coin_auto_bot\logs\scheduled_bot.log
echo ===== START: %date% %time% ===== >> C:\Users\TEST\coin_auto_bot\logs\scheduled_bot.log

REM 가상환경의 Python으로 PAPER 모드 봇을 실행합니다.
C:\Users\TEST\coin_auto_bot\.venv\Scripts\python.exe C:\Users\TEST\coin_auto_bot\main.py >> C:\Users\TEST\coin_auto_bot\logs\scheduled_bot.log 2>&1

REM 프로그램 종료 상태를 기록합니다. 0이면 정상 종료입니다.
echo EXIT CODE: %ERRORLEVEL% >> C:\Users\TEST\coin_auto_bot\logs\scheduled_bot.log
echo ===== END: %date% %time% ===== >> C:\Users\TEST\coin_auto_bot\logs\scheduled_bot.log