# Setup PowerShell script for MexTradeBot-Coordinador Scheduled Task on Windows VPS
#
# Configura una tarea programada que ejecuta run_coordinador.py cada 15 minutos desatendida.

$TaskName = "MTB-Coordinador"   # mismo nombre que la tarea existente en el VPS: la reemplaza
$WorkingDir = "C:\MTB"
$VenvPython = "python"          # Python del sistema, el mismo que usan las demas tareas del VPS
$ScriptPath = "$WorkingDir\run_coordinador.py"

Write-Host "Configurando Tarea Programada: $TaskName..." -ForegroundColor Cyan

# Eliminar tarea previa si existe
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

$Action = New-ScheduledTaskAction -Execute $VenvPython -Argument $ScriptPath -WorkingDirectory $WorkingDir
$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 15)
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RunOnlyIfNetworkAvailable

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "Ejecutor del Agente Coordinador Master Trader cada 15m"

Write-Host "✅ Tarea programada $TaskName creada con éxito." -ForegroundColor Green
