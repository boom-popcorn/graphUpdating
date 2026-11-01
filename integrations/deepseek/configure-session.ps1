param(
    [string]$Model = "deepseek-flash",
    [ValidateSet("enabled", "disabled")]
    [string]$Thinking = "disabled",
    [int]$MaxTokens = 4096,
    [switch]$TextOnly
)

$ErrorActionPreference = "Stop"

if (-not $env:DEEPSEEK_API_KEY) {
    $secureKey = Read-Host "DeepSeek API key（仅保存到当前进程环境）" -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
    try {
        $env:DEEPSEEK_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

if (-not $env:DEEPSEEK_API_KEY) {
    throw "DeepSeek API key is empty."
}

$env:ODKG_LLM_BASE_URL = "https://api.deepseek.com"
$env:ODKG_LLM_MODEL = $Model
$env:ODKG_LLM_API_KEY = $env:DEEPSEEK_API_KEY
$env:ODKG_LLM_THINKING = $Thinking
$env:ODKG_LLM_MAX_TOKENS = [string]$MaxTokens
$env:ODKG_LLM_TIMEOUT = "180"

if (-not $TextOnly) {
    $env:ODKG_VLM_BASE_URL = "https://api.deepseek.com"
    $env:ODKG_VLM_MODEL = $Model
    $env:ODKG_VLM_API_KEY = $env:DEEPSEEK_API_KEY
    $env:ODKG_VLM_THINKING = $Thinking
    $env:ODKG_VLM_MAX_TOKENS = [string]$MaxTokens
    $env:ODKG_VLM_TIMEOUT = "180"
}

Write-Host "DeepSeek session configured."
Write-Host "  model: $Model"
Write-Host "  text adapter: enabled"
Write-Host "  vision adapter: $(-not $TextOnly)"
Write-Host "  key persisted to disk: false"

