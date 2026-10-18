param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("recon", "command-injection", "sensor-spoof", "lateral-movement")]
    [string]$Scenario
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$passwordsPath = Join-Path $projectRoot "Broker\passwords.txt"
$runtimePath = Join-Path $PSScriptRoot "runtime"
$envPath = Join-Path $runtimePath "attacker.env"

$username = switch ($Scenario) {
    "sensor-spoof" { "motion1" }
    "lateral-movement" { "badge1" }
    default { $null }
}

if ($username) {
    if (-not (Test-Path $passwordsPath)) {
        throw "Missing Broker\passwords.txt. Create the broker credentials before running MQTT scenarios."
    }
    $credentialLine = Get-Content $passwordsPath |
        Where-Object { $_ -like "$username`:*" } |
        Select-Object -First 1
    if (-not $credentialLine) {
        throw "No local password found for lab user '$username'."
    }
    $password = ($credentialLine -split ":", 2)[1]
    if (-not $password) {
        throw "The local password for '$username' is empty."
    }
}

New-Item -ItemType Directory -Force -Path $runtimePath | Out-Null
try {
    if ($username) {
        $envContents = "MQTT_USERNAME=$username`nMQTT_PASSWORD=$password`n"
        [System.IO.File]::WriteAllText(
            $envPath,
            $envContents,
            [System.Text.UTF8Encoding]::new($false)
        )
    }
    Push-Location $projectRoot
    try {
        if ($username) {
            docker compose --env-file $envPath --profile tools run --rm attacker `
                --scenario $Scenario
        } else {
            docker compose --profile tools run --rm attacker --scenario $Scenario
        }
        if ($LASTEXITCODE -ne 0) {
            throw "Scenario '$Scenario' failed (exit code $LASTEXITCODE)."
        }
    } finally {
        Pop-Location
    }
} finally {
    if (Test-Path $envPath) {
        Remove-Item -LiteralPath $envPath -Force
    }
}
