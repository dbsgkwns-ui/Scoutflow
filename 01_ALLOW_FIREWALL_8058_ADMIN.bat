@echo off
chcp 65001 >nul
echo ========================================================
echo ScoutFlow 8058 Windows Firewall Allow Rule
echo 관리자 권한으로 실행해야 합니다.
echo ========================================================
netsh advfirewall firewall add rule name="ScoutFlow 8058" dir=in action=allow protocol=TCP localport=8058
if errorlevel 1 (
  echo.
  echo 방화벽 규칙 추가 실패. 이 파일을 우클릭 후 "관리자 권한으로 실행"하세요.
) else (
  echo.
  echo 방화벽 TCP 8058 허용 완료.
)
pause
