# Registra colector_velas.py como Scheduled Task persistente (velas de XM -> servidor, tiempo real).
# Ejecutar UNA SOLA VEZ en el VPS desde PowerShell como Administrador, despues de `git pull` en C:\MTB:
#   powershell -ExecutionPolicy Bypass -File C:\MTB\setup_colector_task.ps1
# Antes: agregar MTB_SERVICE_KEY=<la misma clave del servidor> a C:\MTB\.env y probar con
#   python colector_velas.py --seco --una-vez

$TASK_NAME  = 'MTB-ColectorVelas'
$WORK_DIR   = 'C:\MTB'
$PYTHON     = (Get-Command python -ErrorAction Stop).Source
$SCRIPT     = 'colector_velas.py'

Unregister-ScheduledTask -TaskName $TASK_NAME -Confirm:$false -ErrorAction SilentlyContinue

$action   = New-ScheduledTaskAction -Execute $PYTHON -Argument $SCRIPT -WorkingDirectory $WORK_DIR
$trigger  = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -RunLevel Highest

Register-ScheduledTask `
    -TaskName  $TASK_NAME `
    -Action    $action `
    -Trigger   $trigger `
    -Settings  $settings `
    -Principal $principal `
    -Force | Out-Null

Start-ScheduledTask -TaskName $TASK_NAME
Write-Host "OK - tarea '$TASK_NAME' registrada y arrancada."
Write-Host "Verifica con: Get-ScheduledTask -TaskName '$TASK_NAME' | Get-ScheduledTaskInfo"
Write-Host "Para apagarlo (el servidor sigue con Dukascopy): Stop-ScheduledTask -TaskName '$TASK_NAME'; Disable-ScheduledTask -TaskName '$TASK_NAME'"
