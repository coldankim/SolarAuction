# 태양광 경매 공매 알림: Windows 작업 스케줄러 등록 (PC에서 2시간마다 수집·알림)
# 매일 08:17부터 22:17까지 2시간마다 pc_run.py 실행. PC가 꺼져 있던 시간은 켜진 뒤 한 번 실행.
#   등록:  powershell -ExecutionPolicy Bypass -File pc_task_install.ps1
#   해제:  Unregister-ScheduledTask -TaskName SolarAuction -Confirm:$false
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $env:LOCALAPPDATA "Python\bin\pythonw.exe"
if (-not (Test-Path $py)) { $py = (Get-Command pythonw.exe -ErrorAction Stop).Source }

$action = New-ScheduledTaskAction -Execute $py -Argument "pc_run.py" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -Daily -At "08:17"
$rep = New-ScheduledTaskTrigger -Once -At "08:17" -RepetitionInterval (New-TimeSpan -Hours 2) -RepetitionDuration (New-TimeSpan -Hours 14 -Minutes 1)
$trigger.Repetition = $rep.Repetition
$trigger.Repetition.StopAtDurationEnd = $false  # 22:17 실행이 반복 종료 시각에 끊기지 않게
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "SolarAuction" -Description "태양광 경매 공매 알림 (pc_run.py)" `
    -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Get-ScheduledTask -TaskName "SolarAuction" | Get-ScheduledTaskInfo | Select-Object TaskName, NextRunTime
