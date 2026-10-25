@echo off
rem DXVK: runs the game's Direct3D 9 over Vulkan (often more FPS). Run again to turn it off.
cd /d "%~dp0"
if exist "d3d9.dll" (
    del "d3d9.dll"
    echo DXVK OFF - Direct3D 9 classique / classic Direct3D 9
) else (
    copy /y "reborn\dxvk\d3d9.dll" "d3d9.dll" >nul
    echo DXVK ON - Direct3D 9 sur Vulkan / Direct3D 9 over Vulkan
    echo Si le jeu plante ou s'affiche mal, relance ce fichier pour le desactiver.
    echo If the game crashes or looks wrong, run this file again to turn it off.
)
pause
