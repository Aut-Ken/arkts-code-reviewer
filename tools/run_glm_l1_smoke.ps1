[CmdletBinding()]
param(
    [int]$Limit = 3,
    [string]$Model = "glm-5.1",
    [ValidateSet("enabled", "disabled", "none", "omit")]
    [string]$Thinking = "enabled",
    [string]$Category = "animation_examples",
    [string]$SampleId = "",
    [int]$MaxSourceLines = 120,
    [int]$MaxTokens = 3000,
    [int]$TimeoutSeconds = 600,
    [int]$RetryAttempts = 4,
    [double]$RetryBaseDelaySeconds = 20.0,
    [double]$RequestDelaySeconds = 5.0,
    [string]$BaseUrl = "https://open.bigmodel.cn/api/coding/paas/v4",
    [ValidateSet("json_object", "none", "omit")]
    [string]$ResponseFormat = "json_object",
    [string]$RawResponseDir = "reports\parser_validation\raw_glm",
    [string]$Output = "reports\parser_validation\glm_findings_l1_glm51_smoke.jsonl",
    [string]$PrettyOutput = "reports\parser_validation\glm_findings_l1_glm51_smoke.pretty.json",
    [switch]$Resume,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Push-Location $repoRoot

$hadApiKey = Test-Path Env:\GLM_API_KEY
$oldApiKey = $env:GLM_API_KEY
$hadModel = Test-Path Env:\GLM_MODEL
$oldModel = $env:GLM_MODEL
$hadBaseUrl = Test-Path Env:\GLM_BASE_URL
$oldBaseUrl = $env:GLM_BASE_URL
$hadThinking = Test-Path Env:\GLM_THINKING_TYPE
$oldThinking = $env:GLM_THINKING_TYPE
$hadResponseFormat = Test-Path Env:\GLM_RESPONSE_FORMAT
$oldResponseFormat = $env:GLM_RESPONSE_FORMAT
$hadRawResponseDir = Test-Path Env:\GLM_RAW_RESPONSE_DIR
$oldRawResponseDir = $env:GLM_RAW_RESPONSE_DIR
$hadMaxTokens = Test-Path Env:\GLM_MAX_TOKENS
$oldMaxTokens = $env:GLM_MAX_TOKENS
$hadRetryAttempts = Test-Path Env:\GLM_RETRY_ATTEMPTS
$oldRetryAttempts = $env:GLM_RETRY_ATTEMPTS
$hadRetryBaseDelaySeconds = Test-Path Env:\GLM_RETRY_BASE_DELAY_SECONDS
$oldRetryBaseDelaySeconds = $env:GLM_RETRY_BASE_DELAY_SECONDS

try {
    if (-not $DryRun -and -not $env:GLM_API_KEY) {
        $secureKey = Read-Host "Enter GLM API key for this smoke run" -AsSecureString
        $keyPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
        try {
            $env:GLM_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPtr)
        }
        finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPtr)
        }
    }

    $env:GLM_MODEL = $Model
    $env:GLM_BASE_URL = $BaseUrl
    $env:GLM_THINKING_TYPE = $Thinking
    $env:GLM_RESPONSE_FORMAT = $ResponseFormat
    if ($RawResponseDir) {
        $env:GLM_RAW_RESPONSE_DIR = $RawResponseDir
    }
    $env:GLM_MAX_TOKENS = [string]$MaxTokens
    $env:GLM_RETRY_ATTEMPTS = [string]$RetryAttempts
    $env:GLM_RETRY_BASE_DELAY_SECONDS = [string]$RetryBaseDelaySeconds

    Write-Host "GLM L1 smoke run"
    Write-Host "  model: $Model"
    Write-Host "  thinking: $Thinking"
    Write-Host "  response_format: $ResponseFormat"
    Write-Host "  base_url: $BaseUrl"
    Write-Host "  limit: $Limit"
    Write-Host "  category: $Category"
    if ($SampleId) {
        Write-Host "  sample_id: $SampleId"
    }
    Write-Host "  output: $Output"
    Write-Host "  pretty_output: $PrettyOutput"
    Write-Host "  retry_attempts: $RetryAttempts"
    Write-Host "  retry_base_delay_seconds: $RetryBaseDelaySeconds"
    Write-Host "  request_delay_seconds: $RequestDelaySeconds"
    if ($RawResponseDir) {
        Write-Host "  raw_response_dir: $RawResponseDir"
    }

    if (-not $Resume) {
        $outputPath = [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $Output))
        $prettyOutputPath = [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $PrettyOutput))
        $repoPath = [System.IO.Path]::GetFullPath((Get-Location).Path)
        if (-not $outputPath.StartsWith($repoPath, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to overwrite output outside repo: $outputPath"
        }
        if (-not $prettyOutputPath.StartsWith($repoPath, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to overwrite pretty output outside repo: $prettyOutputPath"
        }
        if (Test-Path -LiteralPath $outputPath) {
            Remove-Item -LiteralPath $outputPath -Force
            Write-Host "  cleared existing output"
        }
        if (Test-Path -LiteralPath $prettyOutputPath) {
            Remove-Item -LiteralPath $prettyOutputPath -Force
            Write-Host "  cleared existing pretty output"
        }
    }

    $argsList = @(
        "tools\validate_parser_with_llm.py",
        "--parser", "arkts-tree-sitter",
        "--limit", [string]$Limit,
        "--max-source-lines", [string]$MaxSourceLines,
        "--timeout-seconds", [string]$TimeoutSeconds,
        "--retry-attempts", [string]$RetryAttempts,
        "--retry-base-delay-seconds", [string]$RetryBaseDelaySeconds,
        "--request-delay-seconds", [string]$RequestDelaySeconds,
        "--output", $Output
    )

    if ($PrettyOutput) {
        $argsList += @("--pretty-output", $PrettyOutput)
    }
    if ($Category) {
        $argsList += @("--category", $Category)
    }
    if ($SampleId) {
        $argsList += @("--sample-id", $SampleId)
    }
    if ($Resume) {
        $argsList += "--resume"
    }
    if ($DryRun) {
        $argsList += "--dry-run"
    }

    python @argsList
}
finally {
    if ($hadApiKey) { $env:GLM_API_KEY = $oldApiKey } else { Remove-Item Env:\GLM_API_KEY -ErrorAction SilentlyContinue }
    if ($hadModel) { $env:GLM_MODEL = $oldModel } else { Remove-Item Env:\GLM_MODEL -ErrorAction SilentlyContinue }
    if ($hadBaseUrl) { $env:GLM_BASE_URL = $oldBaseUrl } else { Remove-Item Env:\GLM_BASE_URL -ErrorAction SilentlyContinue }
    if ($hadThinking) { $env:GLM_THINKING_TYPE = $oldThinking } else { Remove-Item Env:\GLM_THINKING_TYPE -ErrorAction SilentlyContinue }
    if ($hadResponseFormat) { $env:GLM_RESPONSE_FORMAT = $oldResponseFormat } else { Remove-Item Env:\GLM_RESPONSE_FORMAT -ErrorAction SilentlyContinue }
    if ($hadRawResponseDir) { $env:GLM_RAW_RESPONSE_DIR = $oldRawResponseDir } else { Remove-Item Env:\GLM_RAW_RESPONSE_DIR -ErrorAction SilentlyContinue }
    if ($hadMaxTokens) { $env:GLM_MAX_TOKENS = $oldMaxTokens } else { Remove-Item Env:\GLM_MAX_TOKENS -ErrorAction SilentlyContinue }
    if ($hadRetryAttempts) { $env:GLM_RETRY_ATTEMPTS = $oldRetryAttempts } else { Remove-Item Env:\GLM_RETRY_ATTEMPTS -ErrorAction SilentlyContinue }
    if ($hadRetryBaseDelaySeconds) { $env:GLM_RETRY_BASE_DELAY_SECONDS = $oldRetryBaseDelaySeconds } else { Remove-Item Env:\GLM_RETRY_BASE_DELAY_SECONDS -ErrorAction SilentlyContinue }
    Pop-Location
}
