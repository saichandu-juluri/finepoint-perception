# Installs FinePoint Perception on this Windows PC, or brings it up to date, and opens it in the browser. The README's one
# line runs it straight from GitHub, and the same line updates it:
#   powershell -ExecutionPolicy Bypass -c "irm -useb https://raw.githubusercontent.com/saichandu-juluri/finepoint-perception/main/install.ps1 | iex"
# Run so, it gets the app into %USERPROFILE%\finepoint-perception with git, or updates it there. Run as a file
# (powershell -ExecutionPolicy Bypass -File install.ps1), it installs the folder it is in, as that folder is. It asks
# nothing (the server address, email and password go into the app's sign-in) and is safe to run again.
# Where pixi is missing it puts pixi 0.83.0, the version that made pixi.lock, into %USERPROFILE%\.pixi\bin (change both
# together, and the checksum below). git comes from conda-forge through pixi, so a fresh PC needs nothing else. It makes
# the FinePoint Perception shortcut in the Start menu and on the desktop; FPP_APPS_DIR names another folder for it (tests).

$repo = 'https://github.com/saichandu-juluri/finepoint-perception.git'
$pixiVersion = '0.83.0'
$pixiZipSha256 = '77bed9f9d2795c94f9ab5744353892eb82bd3b1eaa09ecbbe7573e22ef70a2f4'   # pixi-x86_64-pc-windows-msvc.zip
$gitSpec = 'git==2.56.0'
$again = 'To try again, paste the install line into PowerShell again.'

# Every process that runs from the app's folder: the app itself, an uploader, a step. The running app keeps viewer\.next
# and node_modules open, and the others the environment's files, which the install replaces. An upload carries on when
# the app starts again.
function Stop-App([string] $dir) {
  $mine = @(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith("$dir\", [StringComparison]::OrdinalIgnoreCase) })
  if (-not $mine.Count) { return }
  Write-Host 'Stopping FinePoint Perception, which is running ...'
  foreach ($p in $mine) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
  foreach ($p in $mine) { Wait-Process -Id $p.ProcessId -Timeout 15 -ErrorAction SilentlyContinue }
}

function Install-FinePointPerception {
  $arch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
  if ($arch -ne 'AMD64') { throw 'FinePoint Perception needs a 64-bit PC with an Intel or AMD processor (x64).' }
  $os = [Environment]::OSVersion.Version
  if ($os.Major -lt 10 -or $os.Build -lt 17763) { throw 'FinePoint Perception needs Windows 10 (version 1809 or newer) or Windows 11.' }
  [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
  $ProgressPreference = 'SilentlyContinue'      # the progress bar of Windows PowerShell makes a download many times slower

  $pixi = Join-Path $env:USERPROFILE '.pixi\bin\pixi.exe'
  if (-not (Test-Path -LiteralPath $pixi)) {
    Write-Host "Installing pixi $pixiVersion, the tool that installs the app ..."
    $zip = Join-Path ([IO.Path]::GetTempPath()) "pixi-$pixiVersion-$PID.zip"
    try {
      Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/prefix-dev/pixi/releases/download/v$pixiVersion/pixi-x86_64-pc-windows-msvc.zip" -OutFile $zip
      if ((Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash -ne $pixiZipSha256) { throw 'checksum' }
      Expand-Archive -LiteralPath $zip -DestinationPath (Split-Path $pixi) -Force
    } catch {
      throw "Could not install pixi, the tool that installs the app (check the internet connection). $again"
    } finally {
      Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
    }
  }

  # The app's folder: this file's own when it runs as a file; else %USERPROFILE%\finepoint-perception, got or updated
  # with git.
  $dir = if ($PSCommandPath) { Split-Path -Parent $PSCommandPath } else { Join-Path $env:USERPROFILE 'finepoint-perception' }
  $dir = [IO.Path]::GetFullPath($dir).TrimEnd('\')
  if (-not $PSCommandPath) {
    if (Test-Path -LiteralPath (Join-Path $dir '.git')) {
      Stop-App $dir
      Write-Host 'Getting the new version of the app ...'
      & $pixi exec --spec $gitSpec git -C $dir pull --ff-only
      if ($LASTEXITCODE) { throw "Could not get the new version of the app (check the internet connection). $again" }
    } elseif (Test-Path -LiteralPath $dir) {
      throw "There is a folder $dir already, which git did not make. Move it elsewhere, then paste the install line again."
    } else {
      Write-Host 'Getting the app ...'
      & $pixi exec --spec $gitSpec git clone $repo $dir
      if ($LASTEXITCODE) { throw "Could not get the app (check the internet connection). $again" }
    }
  }
  Stop-App $dir

  Push-Location -LiteralPath $dir
  try {
    Write-Host 'Installing what the app needs ...'
    & $pixi install -e core --locked
    if ($LASTEXITCODE) { throw "Could not install what the app needs (check the internet connection, and that about 3 GB are free). $again" }
    Write-Host 'Building the app ...'
    & $pixi run --frozen -e core viewer-build
    if ($LASTEXITCODE) { throw "Could not build the app. $again" }
  } finally {
    Pop-Location
  }

  # The shortcut runs start-finepoint-perception.pyw with the app's own pythonw.exe, which opens no window.
  $pythonw = Join-Path $dir '.pixi\envs\core\pythonw.exe'
  $start = Join-Path $dir 'start-finepoint-perception.pyw'
  $places = if ($env:FPP_APPS_DIR) { @($env:FPP_APPS_DIR) } else { @([Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Desktop')) }
  try {
    $shell = New-Object -ComObject WScript.Shell
    foreach ($place in $places) {
      [void][IO.Directory]::CreateDirectory($place)
      $link = $shell.CreateShortcut((Join-Path $place 'FinePoint Perception.lnk'))
      $link.TargetPath = $pythonw
      $link.Arguments = "`"$start`""
      $link.WorkingDirectory = $dir
      $link.IconLocation = (Join-Path $dir 'finepoint-perception.ico') + ',0'
      $link.Description = 'Opens FinePoint Perception in the browser'
      $link.Save()
    }
  } catch {
    throw "Could not make the FinePoint Perception shortcut in $($places -join ' and ') ($($_.Exception.Message)). $again"
  }

  Start-Process -FilePath $pythonw -ArgumentList "`"$start`"" -WorkingDirectory $dir
  Write-Host ''
  Write-Host 'FinePoint Perception is installed and opens in your browser. Next time, click FinePoint Perception in the'
  Write-Host 'Start menu or on the desktop.'
}

try {
  Install-FinePointPerception
} catch {
  Write-Host ''
  Write-Host $_.Exception.Message -ForegroundColor Red
  # The README's line runs this in a PowerShell of its own, whose exit code then says it failed. Pasted into an open
  # PowerShell window instead, that window stays open with the message.
  if ([Environment]::GetCommandLineArgs() | Where-Object { $_ -match '^[-/](c|command|f|file|e|ec|encodedcommand)$' }) { exit 1 }
}
