# Run with: irm https://raw.githubusercontent.com/IamAngusU/framewire/main/install.ps1 | iex
& {
    $ErrorActionPreference = 'Stop'
    $version = '0.3.0'
    $release = "https://github.com/IamAngusU/framewire/releases/download/v$version"
    $wheelName = "framewire-$version-py3-none-any.whl"
    $framewireRoot = if ($env:FRAMEWIRE_HOME) { $env:FRAMEWIRE_HOME } else { Join-Path $env:APPDATA 'Framewire' }
    $binDir = if ($env:FRAMEWIRE_BIN_DIR) { $env:FRAMEWIRE_BIN_DIR } else { Join-Path $env:LOCALAPPDATA 'Framewire\bin' }
    $runtime = Join-Path $framewireRoot 'runtime'
    $uvPath = if ($env:FRAMEWIRE_UV) { $env:FRAMEWIRE_UV } else { Join-Path $runtime 'uv\uv.exe' }
    $scratch = Join-Path ([IO.Path]::GetTempPath()) ('framewire-' + [Guid]::NewGuid())
    $savedEnvironment = @{}
    $environmentNames = @('UV_UNMANAGED_INSTALL', 'UV_TOOL_DIR', 'UV_TOOL_BIN_DIR', 'UV_PYTHON_INSTALL_DIR', 'UV_CACHE_DIR')
    foreach ($name in $environmentNames) { $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
    try {
        Write-Host "`nFramewire · preparing your local environment`n" -ForegroundColor Cyan
        New-Item -ItemType Directory -Force -Path $runtime, $binDir, $scratch | Out-Null
        if (-not (Test-Path -LiteralPath $uvPath)) {
            $env:UV_UNMANAGED_INSTALL = Join-Path $runtime 'uv'
            Invoke-RestMethod 'https://astral.sh/uv/install.ps1' | Invoke-Expression
            if (-not (Test-Path -LiteralPath $uvPath)) { throw 'uv could not be installed. Check your internet connection.' }
        }
        $package = if ($env:FRAMEWIRE_PACKAGE) { $env:FRAMEWIRE_PACKAGE } else { Join-Path $scratch $wheelName }
        if (-not $env:FRAMEWIRE_PACKAGE) {
            Invoke-WebRequest "$release/$wheelName" -OutFile $package -UseBasicParsing -TimeoutSec 180
            $checksums = Invoke-RestMethod "$release/SHA256SUMS" -TimeoutSec 30
            $checksumLine = ($checksums -split "`n" | Where-Object { $_.Trim().EndsWith("  $wheelName") })
            $expected = ($checksumLine -split '\s+')[0]
            $actual = (Get-FileHash -LiteralPath $package -Algorithm SHA256).Hash.ToLowerInvariant()
            if (-not $expected -or $actual -ne $expected) { throw 'The package checksum did not match. Retry the download.' }
        }
        $env:UV_TOOL_DIR = Join-Path $runtime 'tools'
        $env:UV_TOOL_BIN_DIR = $binDir
        $env:UV_PYTHON_INSTALL_DIR = Join-Path $runtime 'python'
        $env:UV_CACHE_DIR = Join-Path $runtime 'cache'
        $installArgs = @('tool', 'install', '--python', '3.12', '--managed-python', '--no-build', '--reinstall', $package)
        $requirementsFile = Join-Path $framewireRoot 'data\runtime-requirements.txt'
        if (Test-Path -LiteralPath $requirementsFile) { $installArgs += @('--with-requirements', $requirementsFile) }
        if ((Test-Path -LiteralPath (Join-Path $framewireRoot 'adapters\hands.py')) -or
            (Test-Path -LiteralPath (Join-Path $framewireRoot 'adapters\lips.py')) -or
            (Test-Path -LiteralPath (Join-Path $framewireRoot 'adapters\hands.py.disabled')) -or
            (Test-Path -LiteralPath (Join-Path $framewireRoot 'adapters\lips.py.disabled'))) {
            $installArgs += @('--with', 'mediapipe>=1.0,<2')
        }
        if ((Test-Path -LiteralPath (Join-Path $framewireRoot 'adapters\voice.py')) -or
            (Test-Path -LiteralPath (Join-Path $framewireRoot 'adapters\voice.py.disabled'))) {
            $installArgs += @('--with', 'faster-whisper>=1.2,<2', '--with', 'sounddevice>=0.5,<1')
        }
        & $uvPath @installArgs
        if ($LASTEXITCODE -ne 0) { throw 'Package installation failed. Check the message above and run this command again.' }
        if ($env:FRAMEWIRE_NO_PATH -ne '1') {
            $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
            if ($binDir -notin ($userPath -split ';')) {
                [Environment]::SetEnvironmentVariable('Path', ($binDir + ';' + $userPath), 'User')
            }
            if ($binDir -notin ($env:Path -split ';')) { $env:Path = $binDir + ';' + $env:Path }
        }
        & (Join-Path $binDir 'framewire.exe')
        if ($LASTEXITCODE -ne 0) { throw 'The package was installed, but its self-check failed. Run the installer again.' }
        Write-Host "`nInstalled. Try: framewire demo" -ForegroundColor Green
    }
    catch {
        Write-Host "`nFramewire installation did not finish: $($_.Exception.Message)" -ForegroundColor Red
        throw
    }
    finally {
        foreach ($name in $environmentNames) { [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], 'Process') }
        # Only remove the exact temporary files created above; no recursive deletion.
        foreach ($name in @($wheelName, 'SHA256SUMS')) {
            $tempFile = Join-Path $scratch $name
            if (Test-Path -LiteralPath $tempFile) { Remove-Item -LiteralPath $tempFile -Force }
        }
        if (Test-Path -LiteralPath $scratch) { Remove-Item -LiteralPath $scratch }
    }
}
