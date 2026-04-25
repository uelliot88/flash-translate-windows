@echo off
chcp 65001 > nul
echo ========================================
echo   Flash Translate - 安裝相依套件
echo ========================================
echo.
echo 正在安裝套件，請稍候...
pip install keyboard pyperclip deep-translator pypinyin pyttsx3 pywin32
echo.
echo ========================================
echo   安裝完成！
echo   請執行 run.bat 或雙擊 run.bat 啟動程式
echo ========================================
pause
