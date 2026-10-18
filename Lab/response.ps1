param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Block", "Unblock", "Watch")]
    [string]$Action,

    [ValidateSet("badge1", "motion1", "lock1", "controller", "backend", "canary")]
    [string]$User,

    [ValidateRange(1, 60)]
    [int]$PollSeconds = 2
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$aclPath = Join-Path $projectRoot "Broker\acl.conf"
$runtimePath = Join-Path $PSScriptRoot "runtime"
$requestPath = Join-Path $runtimePath "response-requests.jsonl"
$resultPath = Join-Path $runtimePath "response-results.jsonl"
$beginMarker = "# BEGIN CYBERRANGE RESPONSE BLOCKS"
$endMarker = "# END CYBERRANGE RESPONSE BLOCKS"

function Read-ResponseState {
    $content = [System.IO.File]::ReadAllLines($aclPath)
    $blocked = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::OrdinalIgnoreCase
    )
    $baseLines = [System.Collections.Generic.List[string]]::new()
    $inside = $false
    foreach ($line in $content) {
        if ($line -eq $beginMarker) {
            $inside = $true
            continue
        }
        if ($line -eq $endMarker) {
            $inside = $false
            continue
        }
        if ($inside) {
            if ($line -match '^user\s+([a-zA-Z0-9_-]+)\s*$') {
                [void]$blocked.Add($Matches[1])
            }
        } else {
            $baseLines.Add($line)
        }
    }
    if ($inside) {
        throw "The response block section in Broker\acl.conf is malformed."
    }
    return @{ Blocked = $blocked; BaseLines = $baseLines }
}

function Write-ResponseState($state) {
    $lines = [System.Collections.Generic.List[string]]::new()
    $lines.Add($beginMarker)
    foreach ($name in ($state.Blocked | Sort-Object)) {
        $lines.Add("# Lab response block for $name")
        $lines.Add("user $name")
        $lines.Add("topic deny #")
    }
    $lines.Add($endMarker)
    $lines.Add("")
    foreach ($line in $state.BaseLines) {
        $lines.Add($line)
    }
    $encoding = [System.Text.UTF8Encoding]::new($false)
    $temporaryPath = "$aclPath.tmp"
    [System.IO.File]::WriteAllLines($temporaryPath, $lines, $encoding)
    Move-Item -LiteralPath $temporaryPath -Destination $aclPath -Force

    $brokerPath = (Resolve-Path (Join-Path $projectRoot "Broker")).Path
    docker run --rm -v "${brokerPath}:/work" --entrypoint sh eclipse-mosquitto:2 `
        -c "chown mosquitto:mosquitto /work/acl.conf && chmod 600 /work/acl.conf"
    if ($LASTEXITCODE -ne 0) {
        throw "Could not set Mosquitto ownership on the updated ACL (exit code $LASTEXITCODE)."
    }
    docker compose -f (Join-Path $projectRoot "docker-compose.yml") restart mqtt-broker
    if ($LASTEXITCODE -ne 0) {
        throw "Could not restart the broker to apply the ACL change (exit code $LASTEXITCODE)."
    }
}

function Add-ResponseResult($request, [string]$status, [string]$message) {
    New-Item -ItemType Directory -Force -Path $runtimePath | Out-Null
    $result = @{
        id = $request.id
        timestamp = [DateTime]::UtcNow.ToString("o")
        action = $request.action
        username = $request.username
        status = $status
        message = $message
    } | ConvertTo-Json -Compress
    [System.IO.File]::AppendAllText(
        $resultPath,
        $result + [Environment]::NewLine,
        [System.Text.UTF8Encoding]::new($false)
    )
}

function Apply-Response([string]$Operation, [string]$Username) {
    if ($Username -notin @("badge1", "motion1", "lock1", "controller", "backend", "canary")) {
        throw "Unknown lab MQTT username '$Username'."
    }
    $state = Read-ResponseState
    if ($Operation -eq "Block") {
        [void]$state.Blocked.Add($Username)
        $message = "Blocked '$Username' in the local broker ACL."
    } else {
        [void]$state.Blocked.Remove($Username)
        $message = "Removed the local broker block for '$Username'."
    }
    Write-ResponseState $state
    Write-Output $message
}

function Process-QueuedRequests {
    if (-not (Test-Path $requestPath)) {
        return
    }
    $completed = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::Ordinal
    )
    if (Test-Path $resultPath) {
        foreach ($line in [System.IO.File]::ReadAllLines($resultPath)) {
            if (-not $line) { continue }
            try {
                $result = $line | ConvertFrom-Json -ErrorAction Stop
                if ($result.id) { [void]$completed.Add([string]$result.id) }
            } catch {
                Write-Warning "Ignoring malformed response result record."
            }
        }
    }
    foreach ($line in [System.IO.File]::ReadAllLines($requestPath)) {
        if (-not $line) { continue }
        try {
            $request = $line | ConvertFrom-Json -ErrorAction Stop
            if (-not $request.id -or $completed.Contains([string]$request.id)) {
                continue
            }
            if ($request.action -ne "block") {
                throw "Unsupported response action '$($request.action)'."
            }
            Apply-Response "Block" ([string]$request.username) | Out-Host
            Add-ResponseResult $request "applied" "Broker restarted with the user blocked."
            [void]$completed.Add([string]$request.id)
        } catch {
            Write-Warning "Could not apply queued response: $($_.Exception.Message)"
            if ($request -and $request.id -and -not $completed.Contains([string]$request.id)) {
                Add-ResponseResult $request "failed" $_.Exception.Message
                [void]$completed.Add([string]$request.id)
            }
        }
    }
}

if (-not (Test-Path $aclPath)) {
    throw "Broker ACL file not found: $aclPath"
}

if ($Action -eq "Watch") {
    Write-Output "Watching local response requests; stop with Ctrl+C."
    while ($true) {
        Process-QueuedRequests
        Start-Sleep -Seconds $PollSeconds
    }
}

if (-not $User) {
    throw "-User is required for Block and Unblock."
}
$request = @{
    id = [guid]::NewGuid().ToString()
    action = $Action.ToLowerInvariant()
    username = $User
}
try {
    Apply-Response $Action $User
    Add-ResponseResult $request "applied" "Broker restarted with the requested ACL state."
} catch {
    Add-ResponseResult $request "failed" $_.Exception.Message
    throw
}
