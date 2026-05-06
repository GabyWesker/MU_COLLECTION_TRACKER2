@echo off
REM ===== MU Collection Tracker - Git Sync =====
REM Ejecuta este archivo como Administrador

cd /d "%~dp0"

echo 1. Guardando tus cambios locales...
git stash push -m "backup local"

echo 2. Descargando version del repo...
git fetch origin

echo 3. Restaurando tus archivos...
git stash pop

echo 4. Agregando archivos...
git add -A

echo 5. Creando commit...
git commit -m "feat: Migracion a Neon - Sistema multiusuario con login"

echo 6. Subiendo cambios...
git push origin main

echo.
echo Listo! Revisa tu repo en GitHub.
pause