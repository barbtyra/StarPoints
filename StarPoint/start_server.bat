@echo on
setlocal
REM Ir a la carpeta donde está este .bat
cd /d "%~dp0"

REM 1) Crear venv si no existe
if not exist ".venv\Scripts\python.exe" (
  echo [INFO] Creando entorno virtual...
  py -m venv .venv || (echo [ERROR] Falló la creación del venv & pause & exit /b 1)
)

REM 2) Activar venv
call ".venv\Scripts\activate" || (echo [ERROR] No pude activar el venv & pause & exit /b 1)

REM 3) Asegurar pip actualizado
".\.venv\Scripts\python.exe" -m pip install --upgrade pip setuptools wheel

REM 4) Instalar dependencias si hay requirements.txt
if exist "requirements.txt" (
  echo [INFO] Instalando dependencias...
  ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt
)

REM 5) Lanzar Streamlit (escucha en todas las IP de tu red)
echo [INFO] Iniciando servidor...
".\.venv\Scripts\python.exe" -m streamlit run app.py --server.address=0.0.0.0 --server.port=8501 1>server.log 2>&1

echo.
echo [INFO] El proceso termino. Si se cerro inesperadamente, revisa server.log
pause
