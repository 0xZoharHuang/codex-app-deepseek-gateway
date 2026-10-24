param([ValidateSet('Runner','Target')][string]$Mode='Runner',[Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{16}$')][string]$RunId)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$RunDir=Join-Path (Join-Path (Split-Path $PSScriptRoot -Parent) '.evidence\ui-probe') $RunId
if(!(Test-Path -LiteralPath $RunDir)){throw 'Missing owned run directory'}
function Write-ProbeJson($Name,$Data){$Data|ConvertTo-Json -Depth 10|Set-Content -LiteralPath (Join-Path $RunDir $Name) -Encoding UTF8}
if($Mode -eq 'Target'){
 Add-Type -AssemblyName PresentationFramework,PresentationCore,WindowsBase
 $form=New-Object Windows.Window
 $form.Title='Codex Isolated UI Probe '+$RunId;$form.Width=500;$form.Height=220
 $form.Left=40;$form.Top=40;$form.ShowInTaskbar=$false;$form.ShowActivated=$false;$form.ResizeMode='NoResize'
 $panel=New-Object Windows.Controls.StackPanel;$panel.Margin='20'
 $text=New-Object Windows.Controls.TextBox
 $text.Name='ProbeInput';$text.Height=30
 [Windows.Automation.AutomationProperties]::SetName($text,'Probe input')
 $button=New-Object Windows.Controls.Button
 $button.Content='Apply probe';$button.Height=30;$button.Margin='0,10,0,10'
 [Windows.Automation.AutomationProperties]::SetName($button,'Apply probe')
 $label=New-Object Windows.Controls.TextBlock
 $label.Text='WAITING'
 [Windows.Automation.AutomationProperties]::SetName($label,'Probe result')
 $button.Add_Click({$label.Text='ACK:'+$text.Text;Write-ProbeJson 'target-action.json' @{pid=$PID;run_id=$RunId;text=$text.Text;result=$label.Text}})
 [void]$panel.Children.Add($text);[void]$panel.Children.Add($button);[void]$panel.Children.Add($label);$form.Content=$panel
 $timer=New-Object Windows.Threading.DispatcherTimer;$timer.Interval=[TimeSpan]::FromSeconds(180);$timer.Add_Tick({$timer.Stop();$form.Close()})
 $form.Add_ContentRendered({$p=Get-Process -Id $PID;$helper=New-Object Windows.Interop.WindowInteropHelper($form);Write-ProbeJson 'target-ready.json' @{pid=$PID;session_id=$p.SessionId;started=$p.StartTime.ToUniversalTime().ToString('o');handle=$helper.Handle.ToInt64();title=$form.Title;run_id=$RunId;target_type='WPF'};$timer.Start()})
 $app=New-Object Windows.Application
 try{[void]$app.Run($form)}finally{$timer.Stop()}
 exit 0
}
$proc=$null;$ready=$null
$result=[ordered]@{schema_version=1;run_id=$RunId;started=[DateTime]::UtcNow.ToString('o');runner_pid=$PID;runner_session=(Get-Process -Id $PID).SessionId;identity=[Security.Principal.WindowsIdentity]::GetCurrent().Name;success=$false;steps=@();cleanup=@{}}
try{
 if($result.runner_session -eq 0){throw 'Interactive task unexpectedly in session 0'}
 Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes,System.Drawing
 Add-Type -TypeDefinition @'
using System;using System.Runtime.InteropServices;
public static class OwnedWindowProbe{
 [StructLayout(LayoutKind.Sequential)]public struct RECT{public int Left,Top,Right,Bottom;}
 [DllImport("user32.dll",SetLastError=true)]public static extern bool GetWindowRect(IntPtr h,out RECT r);
 [DllImport("user32.dll",SetLastError=true)]public static extern bool PrintWindow(IntPtr h,IntPtr dc,uint f);
 [DllImport("user32.dll")]public static extern uint GetWindowThreadProcessId(IntPtr h,out uint p);
 [DllImport("user32.dll")]public static extern bool PostMessage(IntPtr h,uint m,IntPtr w,IntPtr l);
}
'@
 $exe=Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
 $argv='-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+$PSCommandPath+'" -Mode Target -RunId '+$RunId
 $proc=Start-Process -FilePath $exe -ArgumentList $argv -PassThru -WindowStyle Hidden
 $deadline=[DateTime]::UtcNow.AddSeconds(25)
 while(!(Test-Path -LiteralPath (Join-Path $RunDir 'target-ready.json'))){if($proc.HasExited){throw ('Target exited '+$proc.ExitCode)};if([DateTime]::UtcNow -gt $deadline){throw 'Target ready timeout'};Start-Sleep -Milliseconds 200}
 $ready=Get-Content -LiteralPath (Join-Path $RunDir 'target-ready.json') -Raw|ConvertFrom-Json
 if($ready.run_id -ne $RunId -or $ready.pid -ne $proc.Id -or $ready.session_id -ne $result.runner_session){throw 'Target identity/session mismatch'}
 $hwnd=[IntPtr][int64]$ready.handle;[uint32]$owner=0
 [void][OwnedWindowProbe]::GetWindowThreadProcessId($hwnd,[ref]$owner)
 if($owner -ne $proc.Id){throw 'Wrong HWND owner'}
 $result.target=$ready;$result.steps+=@{name='owned_target_identity';passed=$true}
 $element=[Windows.Automation.AutomationElement]::FromHandle($hwnd)
 if($element.Current.ProcessId -ne $proc.Id){throw 'Wrong UIA process'}
 $all=$element.FindAll([Windows.Automation.TreeScope]::Descendants,[Windows.Automation.Condition]::TrueCondition)
 $tree=@();foreach($el in $all){$tree+=@{name=$el.Current.Name;automation_id=$el.Current.AutomationId;control_type=$el.Current.ControlType.ProgrammaticName;process_id=$el.Current.ProcessId;enabled=$el.Current.IsEnabled;offscreen=$el.Current.IsOffscreen}}
 Write-ProbeJson 'uia-tree.json' @{root=@{name=$element.Current.Name;process_id=$element.Current.ProcessId};descendants=$tree}
 $inputControl=$element.FindFirst([Windows.Automation.TreeScope]::Descendants,(New-Object Windows.Automation.PropertyCondition([Windows.Automation.AutomationElement]::NameProperty,'Probe input')))
 $button=$element.FindFirst([Windows.Automation.TreeScope]::Descendants,(New-Object Windows.Automation.PropertyCondition([Windows.Automation.AutomationElement]::NameProperty,'Apply probe')))
 if($null -eq $inputControl -or $null -eq $button){throw 'Missing UIA controls'}
 $value=$inputControl.GetCurrentPattern([Windows.Automation.ValuePattern]::Pattern);$expected='cds-ui-'+$RunId
 $value.SetValue($expected)
 if($value.Current.Value -ne $expected){throw 'UIA text mismatch'}
 $result.steps+=@{name='uia_scoped_value_write_readback';passed=$true}
 $invoke=$button.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern);$invoke.Invoke()
 $deadline=[DateTime]::UtcNow.AddSeconds(8)
 while(!(Test-Path -LiteralPath (Join-Path $RunDir 'target-action.json'))){if([DateTime]::UtcNow -gt $deadline){throw 'UIA action timeout'};Start-Sleep -Milliseconds 100}
 $action=Get-Content -LiteralPath (Join-Path $RunDir 'target-action.json') -Raw|ConvertFrom-Json
 if($action.pid -ne $proc.Id -or $action.run_id -ne $RunId -or $action.result -ne ('ACK:'+$expected)){throw 'UIA Invoke readback mismatch'}
 $result.steps+=@{name='uia_scoped_button_invoke';passed=$true;ack=$action.result}
 $rect=New-Object OwnedWindowProbe+RECT
 if(![OwnedWindowProbe]::GetWindowRect($hwnd,[ref]$rect)){throw 'GetWindowRect failed'}
 $width=$rect.Right-$rect.Left;$height=$rect.Bottom-$rect.Top
 if($width -lt 1 -or $height -lt 1 -or $width -gt 4096 -or $height -gt 4096){throw 'Invalid dimensions'}
 $bmp=New-Object Drawing.Bitmap($width,$height);$graphics=[Drawing.Graphics]::FromImage($bmp);$hdc=$graphics.GetHdc()
 try{$printed=[OwnedWindowProbe]::PrintWindow($hwnd,$hdc,0)}finally{$graphics.ReleaseHdc($hdc);$graphics.Dispose()}
 $png=Join-Path $RunDir 'owned-window.png';$bmp.Save($png,[Drawing.Imaging.ImageFormat]::Png)
 $colors=New-Object 'Collections.Generic.HashSet[int]'
 for($x=0;$x -lt $width;$x+=5){for($y=0;$y -lt $height;$y+=5){[void]$colors.Add($bmp.GetPixel($x,$y).ToArgb())}}
 $bmp.Dispose()
 $shot=@{name='printwindow';passed=($printed -and $colors.Count -gt 2);api_return=$printed;width=$width;height=$height;sampled_colors=$colors.Count;sha256=(Get-FileHash -LiteralPath $png -Algorithm SHA256).Hash.ToLowerInvariant();bytes=(Get-Item -LiteralPath $png).Length}
 $result.steps+=$shot
 if(!$shot.passed){throw 'PrintWindow lacks rendered image'}
 $result.success=$true
}catch{$result.error=@{message=$_.Exception.Message;type=$_.Exception.GetType().FullName;stack=$_.ScriptStackTrace}}
finally{
 if($null -ne $proc){
  $proc.Refresh()
  if(!$proc.HasExited){
   if($null -ne $ready){[uint32]$ownerNow=0;[void][OwnedWindowProbe]::GetWindowThreadProcessId([IntPtr][int64]$ready.handle,[ref]$ownerNow);if($ownerNow -eq $proc.Id){[void][OwnedWindowProbe]::PostMessage([IntPtr][int64]$ready.handle,0x10,[IntPtr]::Zero,[IntPtr]::Zero)}}
   if(!$proc.WaitForExit(8000)){$proc.Kill();[void]$proc.WaitForExit(5000)}
  }
  $result.cleanup=@{owned_pid=$proc.Id;exited=$proc.HasExited}
 }
 $result.finished=[DateTime]::UtcNow.ToString('o');Write-ProbeJson 'result.json' $result
}
if(!$result.success){exit 1}
