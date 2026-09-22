# Registra servidor_local.py como Scheduled Task persistente.
# Ejecutar UNA SOLA VEZ en el VPS desde PowerShell como Administrador:
#   powershell -ExecutionPolicy Bypass -File C:\MTB\setup_servidor_task.ps1

$TASK_NAME  = 'MTB-ServidorLocal'
$WORK_DIR   = 'C:\MTB'
$PYTHON     = (Get-Command python -ErrorAction Stop).Source
$SCRIPT     = 'servidor_local.py'

# Elimina version anterior si existe
Unregister-ScheduledTask -TaskName $TASK_NAME -Confirm:$false -ErrorAction SilentlyContinue

$action   = New-ScheduledTaskAction -Execute $PYTHON -Argument $SCRIPT -WorkingDirectory $WORK_DIR
$trigger  = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 2) `
    -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -RunLevel Highest

Register-ScheduledTask `
    -TaskName  $TASK_NAME `
    -Action    $action `
    -Trigger   $trigger `
    -Settings  $settings `
    -Principal $principal `
    -Force | Out-Null

# Arranca inmediatamente
Start-ScheduledTask -TaskName $TASK_NAME
Write-Host "OK — tarea '$TASK_NAME' registrada y arrancada."
Write-Host "Verifica con: Get-ScheduledTask -TaskName '$TASK_NAME' | Get-ScheduledTaskInfo"
