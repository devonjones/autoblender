# STL Tools: one tab per tool the autoblender API offers (GET /ops). Drop STL files or
# folders on a tab; each file goes up to S3, the Lambda runs the tool, and the result
# lands in a sibling folder. Windows PowerShell 5.1, run via "STL Tools.cmd".
# Config: .env next to this script, with AB_URL=... and AB_KEY=... (bin/package-stl-tools
# writes it). Keep this file ASCII: 5.1 reads BOM-less scripts as ANSI.

Add-Type -AssemblyName System.Windows.Forms, System.Drawing, System.Net.Http
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$MaxJobs = 4    # matches the Lambda's reserved concurrency
$MaxBusy = 5    # a file is marked failed on its 5th 429

function Fail($msg) {
    [void][Windows.Forms.MessageBox]::Show($msg, 'STL Tools', 'OK', 'Error')
    exit 1
}

$cfg = @{}
$envFile = Join-Path $PSScriptRoot '.env'
if (-not (Test-Path -LiteralPath $envFile)) { Fail "Missing $envFile" }
foreach ($line in [IO.File]::ReadAllLines($envFile)) {
    if ($line -match '^\s*([A-Z_]+)\s*=\s*(.*?)\s*$') { $cfg[$Matches[1]] = $Matches[2] }
}
if (-not $cfg.AB_URL -or -not $cfg.AB_KEY) { Fail '.env needs AB_URL and AB_KEY lines.' }
$Api = $cfg.AB_URL.TrimEnd('/')

$http = New-Object Net.Http.HttpClient
$http.Timeout = [TimeSpan]::FromMinutes(16)   # a run can take the Lambda's full 15

# --- HTTP ---------------------------------------------------------------------------

# Starts a request and returns its task. -Key adds the API key: it goes to the API only,
# never to the S3 links.
function Send($method, $url, $content, [switch]$Key) {
    $req = New-Object Net.Http.HttpRequestMessage ([Net.Http.HttpMethod]::new($method)), $url
    if ($Key) { $req.Headers.Add('X-Api-Key', $cfg.AB_KEY) }
    if ($content) { $req.Content = $content }
    $http.SendAsync($req)
}

function Json($obj) {
    New-Object Net.Http.StringContent (($obj | ConvertTo-Json -Compress -Depth 5), [Text.Encoding]::UTF8, 'application/json')
}

# The response of a finished task, or throws with the server's own message.
function Response($task) {
    if ($task.IsFaulted) { throw $task.Exception.GetBaseException().Message }
    if ($task.IsCanceled) { throw 'timed out' }
    $r = $task.Result
    if (-not $r.IsSuccessStatusCode) {
        $code = [int]$r.StatusCode
        $msg = $r.Content.ReadAsStringAsync().Result
        try { $msg = ($msg | ConvertFrom-Json).error } catch {}
        if ($code -eq 401) { $msg = 'API key rejected - check .env' }
        throw "HTTP ${code}: $msg"
    }
    $r
}

function Read-Json($task) { (Response $task).Content.ReadAsStringAsync().Result | ConvertFrom-Json }

$t = Send GET "$Api/ops" -Key
try { [void]$t.Wait() } catch {}
try { $Ops = @((Read-Json $t).ops) } catch { Fail "Can't reach the STL Tools service:`n`n$_" }

# --- jobs ---------------------------------------------------------------------------
# Each job walks: queued -> creating -> uploading -> running -> downloading -> done.
# The timer advances a job whenever its request task completes; nothing blocks the UI.

$Jobs = New-Object Collections.ArrayList
$Active = @('creating', 'uploading', 'running', 'downloading')
$Shown = @{ queued = 'queued'; creating = 'uploading'; uploading = 'uploading'; running = 'working';
            downloading = 'downloading'; done = 'done'; failed = 'FAILED' }

function Add-Job($op, $opts, $src, $dst) {
    $item = New-Object Windows.Forms.ListViewItem 'queued'
    [void]$item.SubItems.AddRange([string[]]@($op.label, $src, ''))
    [void]$list.Items.Add($item)
    [void]$Jobs.Add([pscustomobject]@{
        Op = $op.name; Options = $opts; Src = $src; Dst = $dst; Step = 'queued'
        Task = $null; Id = ''; Stream = $null; Item = $item; NotBefore = [datetime]::MinValue; Busy = 0
    })
}

# Folder C:\x\walls -> C:\x\walls.<op>\<same subfolders>; file C:\x\a.stl -> C:\x\<op>\a.stl
function Queue-Path($op, $opts, $path) {
    if (Test-Path -LiteralPath $path -PathType Container) {
        $root = $path.TrimEnd('\')
        $out = "$root.$($op.name)"
        foreach ($f in Get-ChildItem -LiteralPath $root -Recurse -File -Filter *.stl) {
            $rel = $f.FullName.Substring($root.Length + 1)
            if (($rel -split '\\') -contains $op.name) { continue }   # output of an earlier single-file drop
            Add-Job $op $opts $f.FullName (Join-Path $out $rel)
        }
    }
    elseif ($path -like '*.stl') {
        Add-Job $op $opts $path (Join-Path (Join-Path (Split-Path $path) $op.name) (Split-Path $path -Leaf))
    }
}

function Set-Step($j, $step, $msg) {
    $j.Step = $step
    $j.Item.Text = $Shown[$step]
    if ($null -ne $msg) { $j.Item.SubItems[3].Text = $msg }
    $j.Item.ForeColor = if ($step -eq 'failed') { [Drawing.Color]::Firebrick } else { [Drawing.SystemColors]::WindowText }
}

function Advance($j) {
    switch ($j.Step) {
        'queued' {
            $j.Task = Send POST "$Api/jobs" (Json @{}) -Key
            Set-Step $j 'creating' ''
        }
        'creating' {
            $d = Read-Json $j.Task
            $j.Id = $d.id
            $j.Stream = [IO.File]::OpenRead($j.Src)
            $j.Task = Send PUT $d.upload_url (New-Object Net.Http.StreamContent $j.Stream)
            Set-Step $j 'uploading'
        }
        'uploading' {
            $j.Stream.Dispose(); $j.Stream = $null
            [void](Response $j.Task)
            $j.Task = Send POST "$Api/jobs/$($j.Id)/run" (Json @{ op = $j.Op; options = $j.Options }) -Key
            Set-Step $j 'running'
        }
        'running' {
            $d = Read-Json $j.Task
            $j.Item.SubItems[3].Text = "$($d.seconds)s"
            $j.Task = Send GET $d.download_url
            Set-Step $j 'downloading'
        }
        'downloading' {
            $bytes = (Response $j.Task).Content.ReadAsByteArrayAsync().Result
            [void](New-Item -ItemType Directory -Force -Path (Split-Path $j.Dst))
            [IO.File]::WriteAllBytes($j.Dst, $bytes)
            $j.Task = $null
            Set-Step $j 'done'
        }
    }
}

# Advance, turning any error into a failed job. A 429 means every Lambda slot is busy
# (another client, or a quit client's runs still finishing): requeue after a pause.
function Try-Advance($j) {
    try { Advance $j }
    catch {
        if ($j.Stream) { $j.Stream.Dispose(); $j.Stream = $null }
        $j.Task = $null
        if ("$_" -like 'HTTP 429*' -and ++$j.Busy -lt $MaxBusy) {
            $j.NotBefore = (Get-Date).AddSeconds(15)
            Set-Step $j 'queued' 'busy, retrying'
        }
        else { Set-Step $j 'failed' "$_" }
    }
}

function Tick {
    foreach ($j in @($Jobs)) {
        if ($Active -contains $j.Step -and $j.Task.IsCompleted) { Try-Advance $j }
    }
    $busy = @($Jobs | Where-Object { $Active -contains $_.Step }).Count
    $now = Get-Date
    foreach ($j in @($Jobs | Where-Object { $_.Step -eq 'queued' -and $_.NotBefore -le $now })) {
        if ($busy -ge $MaxJobs) { break }
        Try-Advance $j
        $busy++
    }
    $n = @{}
    foreach ($j in $Jobs) { $n[$j.Step]++ }
    $lblStatus.Text = "{0} queued, {1} working, {2} done, {3} failed" -f [int]$n.queued, $busy, [int]$n.done, [int]$n.failed
}

# --- UI -----------------------------------------------------------------------------

function Btn($text, [scriptblock]$onClick) {
    $b = New-Object Windows.Forms.Button -Property @{ Text = $text; AutoSize = $true }
    $b.add_Click($onClick)
    $b
}

$form = New-Object Windows.Forms.Form -Property @{
    Text = 'STL Tools'; Width = 1000; Height = 700; StartPosition = 'CenterScreen'
    Font = New-Object Drawing.Font('Segoe UI', 9)
}

$tabs = New-Object Windows.Forms.TabControl -Property @{ Dock = 'Top'; Height = 260 }
foreach ($op in $Ops) {
    $page = New-Object Windows.Forms.TabPage -Property @{ Text = $op.label; Padding = '8,8,8,8' }
    $help = New-Object Windows.Forms.Label -Property @{ Text = $op.help; Dock = 'Top'; Height = 26 }
    $optBar = New-Object Windows.Forms.FlowLayoutPanel -Property @{ Dock = 'Top'; AutoSize = $true }
    $boxes = @{}
    foreach ($o in $op.options) {
        $cb = New-Object Windows.Forms.CheckBox -Property @{ Text = $o.label; Checked = [bool]$o.default; AutoSize = $true }
        $boxes[$o.name] = $cb
        $optBar.Controls.Add($cb)
    }
    $drop = New-Object Windows.Forms.Label -Property @{
        Text = 'Drop STL files or folders here'; Dock = 'Fill'; TextAlign = 'MiddleCenter'
        BorderStyle = 'FixedSingle'; AllowDrop = $true; BackColor = [Drawing.Color]::WhiteSmoke
        Font = New-Object Drawing.Font('Segoe UI', 14)
    }
    $drop.Tag = @{ Op = $op; Boxes = $boxes }
    $drop.add_DragEnter({
        param($s, $e)
        if ($e.Data.GetDataPresent([Windows.Forms.DataFormats]::FileDrop)) { $e.Effect = 'Copy' }
    })
    $drop.add_DragDrop({
        param($s, $e)
        $t = $s.Tag
        $opts = @{}
        foreach ($k in $t.Boxes.Keys) { $opts[$k] = $t.Boxes[$k].Checked }   # options as set at drop time
        foreach ($p in $e.Data.GetData([Windows.Forms.DataFormats]::FileDrop)) { Queue-Path $t.Op $opts $p }
    })
    $page.Controls.AddRange(@($drop, $optBar, $help))   # Fill first, so the Top bars dock above it
    $tabs.TabPages.Add($page)
}

$list = New-Object Windows.Forms.ListView -Property @{ Dock = 'Fill'; View = 'Details'; FullRowSelect = $true }
[void]$list.Columns.Add('Status', 90)
[void]$list.Columns.Add('Tool', 100)
[void]$list.Columns.Add('File', 560)
[void]$list.Columns.Add('Result', 200)
$list.add_DoubleClick({
    foreach ($j in $Jobs) {
        if ($j.Item.Selected -and $j.Step -eq 'done') { Start-Process explorer.exe "/select,`"$($j.Dst)`""; break }
    }
})

$bottom = New-Object Windows.Forms.FlowLayoutPanel -Property @{ Dock = 'Bottom'; AutoSize = $true; WrapContents = $false }
$bottom.Controls.Add((Btn 'Retry failed' {
    foreach ($j in $Jobs) { if ($j.Step -eq 'failed') { $j.Busy = 0; Set-Step $j 'queued' '' } }
}))
$bottom.Controls.Add((Btn 'Clear finished' {
    foreach ($j in @($Jobs)) {
        if ('done', 'failed' -contains $j.Step) { $list.Items.Remove($j.Item); $Jobs.Remove($j) }
    }
}))
$lblStatus = New-Object Windows.Forms.Label -Property @{ AutoSize = $true; Margin = '12,7,3,0' }
$bottom.Controls.Add($lblStatus)
$hint = New-Object Windows.Forms.Label -Property @{ AutoSize = $true; Margin = '24,7,3,0'; ForeColor = [Drawing.Color]::Gray
    Text = 'Double-click a finished file to show it in Explorer.' }
$bottom.Controls.Add($hint)

$form.Controls.AddRange(@($list, $tabs, $bottom))

$timer = New-Object Windows.Forms.Timer -Property @{ Interval = 250 }
$timer.add_Tick({
    try { Tick } catch { $lblStatus.Text = "Error: $_" }
})
$form.add_Shown({ $timer.Start() })
$form.add_FormClosing({
    param($s, $e)
    $left = @($Jobs | Where-Object { 'queued', 'creating', 'uploading', 'running', 'downloading' -contains $_.Step }).Count
    if ($left -and [Windows.Forms.MessageBox]::Show("$left file(s) not finished yet. Quit anyway?", 'STL Tools', 'YesNo', 'Warning') -ne 'Yes') {
        $e.Cancel = $true
        return
    }
    $timer.Stop()
})

[void]$form.ShowDialog()
