@echo off
setlocal

set "ROOT=%~dp0"
set "PYTHON=D:\Tools\miniforge3\envs\sendBiaoXunEmail\python.exe"
set "PROXY_HOST=127.0.0.1"
set "PROXY_PORT=1080"

if not exist "%PYTHON%" (
    echo Python not found: %PYTHON%
    exit /b 1
)

if not exist "%ROOT%main_proxy.py" (
    echo main_proxy.py not found in %ROOT%
    exit /b 1
)

set "BIAOXUN_PROXY_ENABLED=true"
set "BIAOXUN_PROXY_SCHEME=socks5"
set "BIAOXUN_PROXY_HOST=%PROXY_HOST%"
set "BIAOXUN_PROXY_PORT=%PROXY_PORT%"
set "BIAOXUN_PROXY_TIMEOUT=3"

echo Checking local SOCKS5 tunnel on %PROXY_HOST%:%PROXY_PORT% ...
curl.exe --silent --show-error --fail --socks5-hostname %PROXY_HOST%:%PROXY_PORT% -I https://www.zhiliaobiaoxun.com/search >nul
if errorlevel 1 (
    echo Proxy check failed.
    echo Start SSH tunnel first:
    echo ssh -N -D %PROXY_PORT% root@10.27.6.149
    exit /b 1
)

echo Proxy is ready. Starting crawler...
"%PYTHON%" "%ROOT%main_proxy.py"

endlocal