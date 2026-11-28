param(
    [string]$KhanhRef = 'origin/khanh',
    [string]$VietRef = 'origin/viet',
    [switch]$InstallGraphDeps,
    [switch]$RunDemo,
    [switch]$LiveRetriever,
    [switch]$FullLegacy
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $repo '.venv\Scripts\python.exe'
if (!(Test-Path $python)) { throw 'Khong tim thay .venv\Scripts\python.exe' }
function CheckExit([string]$label) { if ($LASTEXITCODE -ne 0) { throw "$label failed: $LASTEXITCODE" } }
function ArchivePaths([string]$ref, [string]$zip, [string[]]$paths) {
    & git -C $repo archive --format=zip "--output=$zip" $ref @paths
    CheckExit "git archive $ref"
}
function CopyRelative([string]$from, [string]$to, [string]$relative) {
    $source = Join-Path $from $relative
    if (!(Test-Path $source)) { throw "Missing: $source" }
    $target = Join-Path $to $relative
    New-Item -ItemType Directory -Force -Path (Split-Path $target -Parent) | Out-Null
    Copy-Item -LiteralPath $source -Destination $target -Force
}
Push-Location $repo
try {
    & git fetch --all --prune; CheckExit 'git fetch'
    & git rev-parse $KhanhRef $VietRef; CheckExit 'git refs'
    if ($InstallGraphDeps) {
        & $python -m pip install 'pydantic>=2.5,<3' 'langgraph>=1.0,<2' 'langchain-core>=0.3' 'numpy>=1.26' 'python-docx>=1.1' 'pytest>=8'
        CheckExit 'dependency install'
    }
    & $python -m pytest tests/tv3 -q; CheckExit 'TV3 standalone tests'
    $temp = Join-Path ([System.IO.Path]::GetTempPath()) ('tv3_integration_' + [guid]::NewGuid().ToString('N'))
    $combined = Join-Path $temp 'combined'
    $viet = Join-Path $temp 'viet'
    New-Item -ItemType Directory -Force -Path $combined,$viet | Out-Null
    $kzip = Join-Path $temp 'khanh.zip'
    $vzip = Join-Path $temp 'viet.zip'
    $paths = @('src','tests','data/syllabus_rules.json','pyproject.toml')
    if ($RunDemo -or $LiveRetriever) {
        & git -C $repo cat-file -e "${KhanhRef}:data/processed" 2>$null
        if ($LASTEXITCODE -eq 0) { $paths += 'data/processed' } else { Write-Warning 'No processed corpus in Khánh ref; demo may fail.' }
    }
    ArchivePaths $KhanhRef $kzip $paths
    ArchivePaths $VietRef $vzip @('data/yccd_taxonomy.json','src/rag/models.py','src/rag/exceptions.py')
    Expand-Archive -LiteralPath $kzip -DestinationPath $combined -Force
    Expand-Archive -LiteralPath $vzip -DestinationPath $viet -Force
    foreach ($f in @('data/yccd_taxonomy.json','src/rag/models.py','src/rag/exceptions.py')) { CopyRelative $viet $combined $f }
    # Overlay only TV3 files, never copy schemas.py, workflow.py, critic.py or Retriever.
    $files = @(
        'src/agents/profiler.py','src/agents/outline_planner.py','src/agents/detail_designer.py',
        'src/utils/pedagogy_validator.py','src/utils/yccd_adapter.py','src/utils/prompt_builder.py','src/utils/revision_engine.py','src/utils/docx_exporter.py',
        'src/utils/tv3_integration.py','src/utils/tv3_bridge.py',
        'src/prompts/pedagogy_rules.md','src/prompts/bloom_rules.md','src/prompts/competency_rules.md',
        'src/prompts/outline_prompt.txt','src/prompts/detail_prompt.txt','src/prompts/worksheet_prompt.txt','src/prompts/revision_prompt.txt',
        'src/prompts/few_shot/lesson_20.json','src/prompts/few_shot/tv3_worksheet_bank.json','scripts/tv3_demo.py'
    )
    foreach ($f in $files) { CopyRelative $repo $combined $f }
    Get-ChildItem (Join-Path $repo 'tests/tv3') -File -Filter '*.py' | ForEach-Object { CopyRelative $repo $combined ('tests/tv3/' + $_.Name) }
    Push-Location $combined
    try {
        & $python -m pytest tests/tv3 -q -rs; CheckExit 'Team contract tests'
        if ($FullLegacy) {
            & $python -m pytest tests/test_schemas.py tests/test_docx_exporter.py tests/test_worksheet_bank.py -q -rs
            if ($LASTEXITCODE -ne 0) { Write-Warning 'Legacy regression is not green. Review failures; two known old expectations conflict with new validation. See TV3_HANDOFF.md.' }
        }
        if ($RunDemo -or $LiveRetriever) {
            $output = Join-Path $temp 'demo_output'
            $argsDemo = @('-m','scripts.tv3_demo','--output',$output,'--demo-bypass-hitl')
            if ($LiveRetriever) { $argsDemo += '--live-retriever' }
            & $python @argsDemo; CheckExit 'Offline demo'
            Write-Host "Demo: $output"
        }
    } finally { Pop-Location }
    Write-Host "Isolated integration tree: $combined"
    Write-Host 'No branch merge, no commits or push performed.'
} finally { Pop-Location }
