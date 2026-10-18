# Usage:  .\Broker\add-user.ps1 <username> [password]
# Creates Broker\passwordfile if missing, otherwise adds/updates the user.
# Prints the password only after a successful update - store it somewhere safe.
param(
    [Parameter(Mandatory = $true)][string]$User,
    [string]$Password
)
if (-not $Password) {
    $bytes = New-Object byte[] 12
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $Password = ($bytes | ForEach-Object { $_.ToString("x2") }) -join ""
}
$file = Join-Path $PSScriptRoot "passwordfile"
$createFile = -not (Test-Path $file)
if ($createFile) {
    New-Item -ItemType File -Path $file | Out-Null
}
docker run --rm -v "${PSScriptRoot}:/work" --entrypoint sh eclipse-mosquitto:2 `
    -c "chown mosquitto:mosquitto /work/passwordfile && chmod 600 /work/passwordfile"
if ($LASTEXITCODE -ne 0) {
    throw "Could not initialize the password file ownership (exit code $LASTEXITCODE)."
}
$arguments = @("-b", "/work/passwordfile", $User, $Password)
docker run --rm --user mosquitto:mosquitto -v "${PSScriptRoot}:/work" --entrypoint mosquitto_passwd `
    eclipse-mosquitto:2 @arguments
if ($LASTEXITCODE -ne 0) {
    throw "mosquitto_passwd failed for user '$User' (exit code $LASTEXITCODE). The password was not confirmed as saved."
}
Write-Host "$User : $Password"
