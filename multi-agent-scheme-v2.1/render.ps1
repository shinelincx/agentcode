$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$sourcePath = Join-Path $repoRoot 'docs\agentwiki\wiki\方案演进.md'
$outputDir = Join-Path $repoRoot 'multi-agent-scheme-v2.1'
$outputPath = Join-Path $outputDir 'multi-agent-scheme-v2.1.html'
$utf8Strict = [System.Text.UTF8Encoding]::new($false, $true)
$md = [System.IO.File]::ReadAllText($sourcePath, $utf8Strict)
$md = [regex]::Replace($md, '\A---\r?\n[\s\S]*?\r?\n---\r?\n', '')
# Wiki links are source references in the standalone reading edition.
$md = [regex]::Replace($md, '\[\[([^\]]+)\]\]', { param($m) '「' + ($m.Groups[1].Value -split '/')[-1] + '」' })
$html = (ConvertFrom-Markdown -InputObject $md).Html
$html = [regex]::Replace($html, '<h1\b[^>]*>[\s\S]*?</h1>', '')
$html = [regex]::Replace($html, '(<h[3-6])\b[^>]*>', '$1>')
# Keep the export relocatable, including its byte-identical archived snapshot.
$html = [regex]::Replace($html, '<a href="(?!https?://|#)([^"]*)">([\s\S]*?)</a>', { param($m) '<span class="reference" title="仓库引用：' + $m.Groups[1].Value + '">' + $m.Groups[2].Value + '</span>' })
$html = $html -replace '<table>', '<div class="table-wrap" tabindex="0" role="region" aria-label="方案对照表，可横向滚动"><table>' -replace '</table>', '</table></div>'
$chunks = [regex]::Split($html, '(?=<h2\b)')
$toc = [System.Collections.Generic.List[string]]::new()
$sections = [System.Collections.Generic.List[string]]::new()
$sectionNumber = 0
foreach ($chunk in $chunks) {
    if ($chunk -notmatch '<h2') {
        if ($chunk.Trim()) { $sections.Add('<div class="document-status">' + $chunk + '</div>') }
        continue
    }
    $heading = [regex]::Match($chunk, '<h2\b[^>]*>([\s\S]*?)</h2>')
    $sectionNumber++
    $sectionId = 'section-{0:d2}' -f $sectionNumber
    $headingText = $heading.Groups[1].Value
    $titleText = $headingText -replace '^\d+\s*·\s*', ''
    $toc.Add('<li><a href="#' + $sectionId + '"><span>' + ('{0:d2}' -f $sectionNumber) + '</span>' + $titleText + '</a></li>')
    $replacement = '<div class="section-header"><span class="section-num">' + ('{0:d2}' -f $sectionNumber) + '</span><h2>' + $titleText + '</h2></div>'
    $sectionBody = $chunk.Substring($heading.Index + $heading.Length)
    $sections.Add('<section class="section" id="' + $sectionId + '">' + $replacement + $sectionBody + '</section>')
}
if ($sectionNumber -ne 15) { throw "Expected 15 sections; got $sectionNumber" }
$original = [System.IO.File]::ReadAllText((Join-Path $repoRoot 'multi-agent-scheme-v2\multi-agent-scheme-v2.html'), $utf8Strict)
$baseStyle = [regex]::Match($original, '<style>([\s\S]*?)</style>').Groups[1].Value
$css = @'
a { color: #7ab5ff; text-underline-offset: 3px; }
a:hover { color: #b6d7ff; }
a:focus-visible, summary:focus-visible, .table-wrap:focus-visible { outline: 2px solid #69b6ff; outline-offset: 4px; }
.skip-link { position: fixed; top: -80px; left: 1rem; background: #fff; color: #111; padding: .7rem; z-index: 10; }
.skip-link:focus { top: 1rem; }
.hero { padding: 4rem 2rem 3rem; }
.hero-content { max-width: 1160px; }
.hero-badge { color: #f3c465; background: #382d19; border-color: #66522c; }
.hero h1 { max-width: 850px; line-height: 1.3; }
.hero-subtitle { max-width: 860px; }
.hero-meta { gap: .6rem 1.8rem; }
.layout { grid-template-columns: 245px minmax(0,1fr); max-width: 1420px; gap: 3rem; }
.toc { font-size: .84rem; }
.toc summary { cursor: pointer; color: var(--ink); font-weight: 700; margin-bottom: 1rem; }
.toc li { margin-bottom: .5rem; }
.toc a { display: flex; gap: .65rem; line-height: 1.5; }
.toc a span { color: #5f83ab; font-family: Consolas, monospace; flex-shrink: 0; }
.document-status { margin-bottom: 2.5rem; }
blockquote { padding: 1rem 1.2rem; border-left: 3px solid var(--warn); background: #29251d; border-radius: 0 8px 8px 0; color: #d4bd91; }
blockquote p { margin: 0; }
.section { scroll-margin-top: 24px; }
.section p, .section li { color: #acb9c8; }
.section h2 { font-size: 1.55rem; }
.section h3 { font-size: 1.1rem; }
.table-wrap { overflow-x: auto; max-height: none; margin: 1.2rem 0 1.7rem; border: 1px solid var(--rule); border-radius: 9px; }
.table-wrap table { width: 100%; border-collapse: collapse; margin: 0; font-size: .88rem; }
th { background: #202d40; color: #e8edf3; text-align: left; font-weight: 650; }
th, td { padding: .8rem 1rem; vertical-align: top; border-bottom: 1px solid var(--rule); line-height: 1.7; }
td { color: #acb9c8; }
tr:last-child td { border-bottom: 0; }
td:first-child { color: #d6e4f2; min-width: 110px; }
pre { background: #0a0e14; border: 1px solid var(--rule); border-radius: 10px; padding: 1.1rem; overflow-x: auto; margin: 1.3rem 0; font: .83rem/1.85 Consolas,'Microsoft YaHei',monospace; color: #b9d5ee; }
.reference { border-bottom: 1px dotted #5c7289; }
.footer { line-height: 1.9; }
@media(max-width:900px) { .layout { grid-template-columns: minmax(0,1fr); gap: 2rem; padding: 2rem 1.2rem; } .toc { position: relative; top: 0; max-height: none; padding: 1rem; background: var(--bg2); border-radius: 8px; } .toc ul { columns: 2; } .toc li { break-inside: avoid; } .hero { padding: 2.8rem 1.2rem 2rem; } }
@media(max-width:600px) { .toc ul { columns: 1; max-height: 260px; overflow-y: auto; } .hero h1 { font-size: 1.8rem; } .hero-subtitle { font-size: 1rem; } .section h2 { font-size: 1.3rem; } th,td { padding: .65rem .8rem; } table { min-width: 620px; } .section { margin-bottom: 2.8rem; padding-bottom: 2rem; } }
@media(prefers-reduced-motion:reduce) { html { scroll-behavior: auto; } }
@media print { :root { --ink:#111; --muted:#333; --rule:#ccc; } body { background:#fff; color:#111; font-size:10pt; } .hero { background:none; padding:0 0 1rem; } .hero::before,.toc,.skip-link { display:none; } .hero h1 { -webkit-text-fill-color:#111; background:none; font-size:24pt; } .layout { display:block; padding:0; } .section { break-before:auto; } .section p,.section li,td { color:#222; } th { background:#eee; color:#111; } td:first-child { color:#111; } table { min-width:0; } .table-wrap,pre { overflow:visible; } pre { white-space:pre-wrap; background:#f5f5f5; color:#111; } h2,h3 { break-after:avoid; } tr { break-inside:avoid; } blockquote { background:#f5f5f5; color:#222; } a { color:#17579c; } }
'@
$tocHtml = $toc -join "`n"
$bodyHtml = $sections -join "`n"
$document = @"
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="description" content="多 Agent 软件交付体系 V2.1 待评审方案：执行恢复、预算预占、权限隔离、不可变发布及十二项故障验收场景。">
<meta name="source-document" content="docs/agentwiki/wiki/方案演进.md">
<title>多 Agent 软件交付体系技术方案 V2.1</title>
<style>$baseStyle
$css</style>
</head>
<body>
<a class="skip-link" href="#main">跳到正文</a>
<header class="hero"><div class="hero-content">
<span class="hero-badge">技术方案 V2.1 · 2026.11.11 · 待评审提案</span>
<h1>多 Agent 软件交付体系<br>技术方案 V2.1</h1>
<p class="hero-subtitle">保留三层架构，优先验证可靠交付。将执行恢复、预算准入、权限隔离和发布流程落实为明确协议，以实际验收证据推进多项目扩展。</p>
<div class="hero-meta"><span>单项目 → 双项目 → 四项目</span><span>15 个方案章节</span><span>12 项待执行故障验收</span><span>Markdown 与 HTML 同源</span></div>
</div></header>
<div class="layout">
<aside class="toc" aria-label="文档目录"><details open><summary>方案目录 · V2.1</summary><nav><ul>$tocHtml</ul></nav></details></aside>
<main class="content" id="main">$bodyHtml</main>
</div>
<footer class="footer">多 Agent 软件交付体系 · V2.1.0 · 2026-11-11（Asia/Singapore）<br>编辑源：docs/agentwiki/wiki/方案演进.md · 实现与运行验收待执行</footer>
</body></html>
"@
[System.IO.Directory]::CreateDirectory($outputDir) | Out-Null
[System.IO.File]::WriteAllText($outputPath, $document, [System.Text.UTF8Encoding]::new($false))
Write-Output "Rendered $sectionNumber sections: $outputPath"
