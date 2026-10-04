# Run this script in your own terminal. The key is entered by you, not the agent.
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$taskProjectRoot = Split-Path -Parent $PSScriptRoot
$taskPython = Join-Path $taskProjectRoot '.venv\Scripts\python.exe'
$taskHelper = Join-Path $PSScriptRoot 'chatgpt_tunnel.py'
$taskProfile = Join-Path $taskProjectRoot '.agentgranule\chatgpt\profiles\agentgranule-personal.yaml'
if (-not (Test-Path -LiteralPath $taskPython -PathType Leaf)) {
    throw 'The project Python environment is missing.'
}
if (-not (Test-Path -LiteralPath $taskProfile -PathType Leaf)) {
    throw 'The personal tunnel profile is not configured yet.'
}

$taskPreviousKey = $env:CONTROL_PLANE_API_KEY
$taskSecureKey = $null
$taskKeyBuffer = [IntPtr]::Zero
try {
    $taskSecureKey = Read-Host 'Enter the runtime API key for the personal tunnel (input hidden)' -AsSecureString
    if ($taskSecureKey.Length -eq 0) { throw 'No runtime key was entered.' }
    $taskKeyBuffer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($taskSecureKey)
    $env:CONTROL_PLANE_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($taskKeyBuffer)
    & $taskPython -X utf8 $taskHelper doctor
    if ($LASTEXITCODE -ne 0) { throw 'Tunnel diagnostics failed. The connection is not verified.' }
    Write-Host 'Keep this terminal open while using AgentGranule in ChatGPT. Ctrl+C stops the tunnel.'
    & $taskPython -X utf8 $taskHelper run
    if ($LASTEXITCODE -ne 0) { throw 'The tunnel stopped with an error.' }
}
finally {
    if ($null -eq $taskPreviousKey) {
        Remove-Item -LiteralPath Env:CONTROL_PLANE_API_KEY -ErrorAction SilentlyContinue
    }
    else { $env:CONTROL_PLANE_API_KEY = $taskPreviousKey }
    if ($taskKeyBuffer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($taskKeyBuffer)
    }
    if ($null -ne $taskSecureKey) { $taskSecureKey.Dispose() }
}
