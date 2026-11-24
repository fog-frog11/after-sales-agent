# 为仅使用 Docker 的新工作区生成配置；现有 .env 不会被覆盖。
$projectRoot = Split-Path -Parent $PSScriptRoot
$configPath = Join-Path $projectRoot '.env'
if (Test-Path -LiteralPath $configPath) {
    Write-Output '沿用现有 .env。'
    exit 0
}

$randomBytes = New-Object byte[] 24
[System.Security.Cryptography.RandomNumberGenerator]::Fill($randomBytes)
$password = [Convert]::ToHexString($randomBytes).ToLowerInvariant()
$content = @"
DEEPSEEK_API_KEY=
AFTER_SALES_POSTGRES_PASSWORD=$password
AFTER_SALES_POSTGRES_PORT=55432
AFTER_SALES_DOCKER_PORT=8766
MALL_PERSISTENT_DENSE=1
MALL_WINDOW_RERANK=1
"@
try {
    $stream = [System.IO.File]::Open($configPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write)
    try {
        $writer = [System.IO.StreamWriter]::new($stream, [System.Text.UTF8Encoding]::new($false))
        try { $writer.WriteLine($content) } finally { $writer.Dispose() }
    } finally { $stream.Dispose() }
    Write-Output '已生成 .env，数据库口令随机创建；可按需填入 DEEPSEEK_API_KEY。'
} catch [System.IO.IOException] {
    if (Test-Path -LiteralPath $configPath) {
        Write-Output '沿用现有 .env。'
    } else {
        throw
    }
}
