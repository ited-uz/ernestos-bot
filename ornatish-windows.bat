@echo off
rem ErnestOS web — o'rnatish (Windows). Shu faylni ikki marta bosing.
rem Dasturni Documents\PROJECTS\ernestOS web ga ko'chiradi va ishga tushiradi.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0ornatish-windows.ps1"
if errorlevel 1 pause
