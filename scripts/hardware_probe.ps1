<#
.SYNOPSIS
    Hardware probe of the LLM Cross-Compiler Framework (manual fallback for Windows, schema 2).

.DESCRIPTION
    Writes target_hardware_config.txt: KEY=VALUE lines starting with PROBE_SCHEMA=2, exactly the keys of
    configs/hardware_probe_schema.yml in the framework and the same values scripts/hardware_probe.py writes on
    Windows. Use it where Python 3.8 or newer is not installed (for example an air-gapped PC): run it, carry the file
    to the framework and import it there.

    Non-interactive by default: it runs with the rights it was started with, asks nothing and installs nothing.
    -Elevate (opt-in) restarts it with administrator rights first; Windows asks for permission.
    No host name, user name, path, serial number or memory part number is read into the result.

.PARAMETER OutputFile
    File to write (default: target_hardware_config.txt in the current folder); "-" writes to the standard output.

.PARAMETER Elevate
    Opt-in: restart the probe with administrator rights (UAC prompt) and wait for it.

.PARAMETER NoToolchain
    Skip the compiler and build tool versions.
#>
param(
    # File the probe writes ("-": standard output)
    [string]$OutputFile = "target_hardware_config.txt",
    # Opt-in: restart elevated (Windows asks for permission)
    [switch]$Elevate,
    # Skip the compiler and build tool versions
    [switch]$NoToolchain
)

$ProbeVersion = '3.3.0'
if ($OutputFile -eq '') { [Console]::Error.WriteLine('Usage: hardware_probe.ps1 [-OutputFile FILE|-] [-Elevate] [-NoToolchain]'); Exit 2 }
if ($OutputFile -ne '-') {
    # Absolute, because an elevated restart starts in another folder.
    $OutputFile = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputFile)
}

# --- opt-in elevation ------------------------------------------------------------------------------------------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if ($Elevate -and -not $isAdmin) {
    if ($OutputFile -eq '-') { [Console]::Error.WriteLine('-Elevate needs -OutputFile FILE.'); Exit 2 }
    $extra = ''
    if ($NoToolchain) { $extra = ' -NoToolchain' }
    # -Wait: the caller (llm-cli scan --script) reads the output file as soon as this script returns.
    try { $elevated = Start-Process PowerShell -Verb RunAs -Wait -PassThru -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -OutputFile `"$OutputFile`"$extra"; Exit $elevated.ExitCode }
    catch { [Console]::Error.WriteLine('Failed to elevate.'); Exit 1 }
}

$ErrorActionPreference = 'Stop'

# --- helpers ---------------------------------------------------------------------------------------------------
$script:Lines = New-Object System.Collections.Generic.List[string]
$script:Written = @{}
$script:Warnings = New-Object System.Collections.Generic.List[string]
$Invariant = [Globalization.CultureInfo]::InvariantCulture

function Write-Log([string]$Message) { [Console]::Error.WriteLine("[PROBE] $Message") }

function Add-Warning([string]$Code) {
    if (-not $script:Warnings.Contains($Code)) { $script:Warnings.Add($Code) }
}

# Writes a value once (the first value wins); one that does not match the pattern is left out with a warning.
function Put([string]$Key, $Value, [string]$Pattern = '', [switch]$Quiet) {
    if ($null -eq $Value) { return }
    $text = [string]$Value
    if ($text -eq '' -or $script:Written.ContainsKey($Key)) { return }
    if ($text -match "[`r`n]" -or ($Pattern -ne '' -and $text -cnotmatch $Pattern)) {
        if (-not $Quiet) { Add-Warning "not_determined:$Key" }
        return
    }
    $script:Written[$Key] = $text
    $script:Lines.Add("$Key=$text")
}

# Free text: printable ASCII (other characters become spaces), whitespace collapsed, at most $Limit characters.
function Clean-Text($Value, [int]$Limit = 128) {
    if ($null -eq $Value) { return '' }
    $t = (([string]$Value) -replace '[^\x20-\x7E]', ' ') -replace ' {2,}', ' '
    $t = $t.Trim()
    if ($t.Length -gt $Limit) { $t = $t.Substring(0, $Limit).TrimEnd() }
    return $t
}

function Put-Text([string]$Key, $Value, [switch]$Quiet) {
    if ($null -eq $Value -or [string]$Value -eq '') { return }
    $t = Clean-Text $Value
    if ($t -eq '') { if (-not $Quiet) { Add-Warning "not_determined:$Key" }; return }
    Put $Key $t
}

function Set-Flag([string]$Key, [string]$Value) { Put $Key $Value }

function Put-Gpu([int]$Index, [string]$Suffix, $Value, [string]$Pattern = '') {
    if ($null -eq $Value -or [string]$Value -eq '') { return }
    $text = [string]$Value
    if ($text -match "[`r`n]" -or ($Pattern -ne '' -and $text -cnotmatch $Pattern)) { return }
    $script:Lines.Add("GPU$($Index)_$Suffix=$text")
}

function First-Number([string]$Text) {
    if ($Text -match '([0-9]{1,5}(\.[0-9]{1,6}){0,3})') { return $Matches[1] }
    return $null
}

function Get-Reg([string]$Path, [string]$Name) {
    try { return (Get-ItemProperty -Path "Registry::HKEY_LOCAL_MACHINE\$Path" -Name $Name -ErrorAction Stop).$Name }
    catch { return $null }
}

# A tool with an empty stdin and a time limit; $null when it is missing, cannot start or hangs.
function Invoke-Tool([string]$Exe, [string[]]$Arguments = @(), [int]$TimeoutMs = 15000) {
    $cmd = Get-Command $Exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $cmd) { return $null }
    try {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $cmd.Path
        $psi.Arguments = ($Arguments -join ' ')
        $psi.UseShellExecute = $false
        $psi.CreateNoWindow = $true
        $psi.RedirectStandardInput = $true
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $process = [System.Diagnostics.Process]::Start($psi)
        $process.StandardInput.Close()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutMs)) {
            try { $process.Kill() } catch { }
            return $null
        }
        return [pscustomobject]@{ Code = $process.ExitCode; Out = $stdout.Result; Err = $stderr.Result }
    } catch { return $null }
}

function Tool-Text($Result) {
    if ($null -eq $Result) { return $null }
    return ([string]$Result.Out + "`n" + [string]$Result.Err)
}

function First-Line([string]$Text) {
    foreach ($line in ($Text -split "`n")) { if ($line.Trim() -ne '') { return $line.Trim() } }
    return ''
}

# --- tables (copies of configs/hardware_probe_schema.yml; tests compare them) ----------------------------------
$X86Vendors = @{ 'GenuineIntel' = 'intel'; 'AuthenticAMD' = 'amd'; 'HygonGenuine' = 'hygon'; 'CentaurHauls' = 'centaur'; 'Shanghai' = 'zhaoxin' }
$PciVendors = @{ '0x10de' = 'nvidia'; '0x1002' = 'amd'; '0x1022' = 'amd'; '0x8086' = 'intel'; '0x106b' = 'apple'; '0x13b5' = 'arm'; '0x5143' = 'qualcomm'; '0x1010' = 'imagination'; '0x14e4' = 'broadcom' }
$SmbiosTypes = @{ 24 = 'DDR3'; 26 = 'DDR4'; 29 = 'LPDDR3'; 30 = 'LPDDR4'; 34 = 'DDR5'; 35 = 'LPDDR5' }
$RockchipModes = @{ '0x350a' = 'rockusb'; '0x350b' = 'rockusb'; '0x0006' = 'adb-mtp'; '0x0019' = 'adb' }
$Machines = @{ 0x8664 = 'x86_64'; 0xAA64 = 'arm64'; 0x014C = 'x86'; 0x01C4 = 'armv7' }
$ArchAliases = @{ 'amd64' = 'x86_64'; 'x86_64' = 'x86_64'; 'x64' = 'x86_64'; 'x86' = 'x86'; 'arm64' = 'arm64'; 'aarch64' = 'arm64' }
# CPU features: IsProcessorFeaturePresent constant, the lowest Windows build that knows it, architecture.
# TRUE -> ON; FALSE -> OFF from that build on, else UNKNOWN (older builds answer FALSE for constants they do not
# know); no constant -> UNKNOWN. The thresholds are conservative and unverified: a FALSE below them costs speed,
# never a wrong ON. (The schema 1 script asked 40 for AVX-512 - 40 is AVX2 - and 37 for NEON - 37 is SSE4.1.)
$Features = [ordered]@{
    'SUPPORTS_SSE42' = @(38, 22000, 'x86')
    'SUPPORTS_AVX' = @(39, 22000, 'x86')
    'SUPPORTS_AVX2' = @(40, 22000, 'x86')
    'SUPPORTS_BMI2' = @(60, 26100, 'x86')
    'SUPPORTS_F16C' = @($null, $null, 'x86')
    'SUPPORTS_FMA' = @($null, $null, 'x86')
    'SUPPORTS_AVX512' = @(41, 22000, 'x86')
    'SUPPORTS_AVX512_VBMI' = @($null, $null, 'x86')
    'SUPPORTS_AVX512_VNNI' = @($null, $null, 'x86')
    'SUPPORTS_AVX512_BF16' = @($null, $null, 'x86')
    'SUPPORTS_AVX_VNNI' = @($null, $null, 'x86')
    'SUPPORTS_AMX_TILE' = @($null, $null, 'x86')
    'SUPPORTS_AMX_INT8' = @($null, $null, 'x86')
    'SUPPORTS_AMX_BF16' = @($null, $null, 'x86')
    'SUPPORTS_NEON' = @(29, 0, 'arm')
    'SUPPORTS_DOTPROD' = @(43, 22000, 'arm')
    'SUPPORTS_FP16_ARITH' = @(67, 26100, 'arm')
    'SUPPORTS_I8MM' = @(66, 26100, 'arm')
    'SUPPORTS_SVE' = @(46, 26100, 'arm')
    'SUPPORTS_SVE2' = @(47, 26100, 'arm')
    'SUPPORTS_SME' = @(70, 26100, 'arm')
    'SUPPORTS_BF16_ARM' = @($null, $null, 'arm')
    # Probe 3.2.0: the CPU features of architectures Windows does not run (always OFF here).
    'SUPPORTS_RVV' = @($null, $null, 'riscv')
    'SUPPORTS_RV_ZFH' = @($null, $null, 'riscv')
    'SUPPORTS_RV_ZVFH' = @($null, $null, 'riscv')
    'SUPPORTS_RV_ZICBOP' = @($null, $null, 'riscv')
    'SUPPORTS_RV_ZIHINTPAUSE' = @($null, $null, 'riscv')
    'SUPPORTS_RV_ZVFBFWMA' = @($null, $null, 'riscv')
    'SUPPORTS_RV_ZBA' = @($null, $null, 'riscv')
    'SUPPORTS_XTHEADVECTOR' = @($null, $null, 'riscv')
    'SUPPORTS_LSX' = @($null, $null, 'loongarch')
    'SUPPORTS_LASX' = @($null, $null, 'loongarch')
    'SUPPORTS_VSX' = @($null, $null, 'power')
    'SUPPORTS_VXE' = @($null, $null, 's390x')
    'SUPPORTS_VXE2' = @($null, $null, 's390x')
    'SUPPORTS_NNPA' = @($null, $null, 's390x')
}

$P_Version = '^[0-9]{1,5}(\.[0-9]{1,6}){0,3}$'
$P_Dotted = '^[0-9]{1,3}(\.[0-9]{1,4}){1,3}$'

# --- native calls ------------------------------------------------------------------------------------------------
$NativeCode = @"
using System;
using System.Runtime.InteropServices;
public static class LlmProbeNative {
    [DllImport("kernel32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool IsProcessorFeaturePresent(uint feature);

    [StructLayout(LayoutKind.Sequential)]
    public struct MemoryStatusEx {
        public uint Length; public uint MemoryLoad; public ulong TotalPhys; public ulong AvailPhys;
        public ulong TotalPageFile; public ulong AvailPageFile; public ulong TotalVirtual; public ulong AvailVirtual;
        public ulong AvailExtendedVirtual;
    }
    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    static extern bool GlobalMemoryStatusEx(ref MemoryStatusEx status);
    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    static extern bool IsWow64Process2(IntPtr process, out ushort processMachine, out ushort nativeMachine);
    [DllImport("kernel32.dll")]
    static extern IntPtr GetCurrentProcess();

    public static int NativeMachine() {
        try {
            ushort processMachine, nativeMachine;
            if (IsWow64Process2(GetCurrentProcess(), out processMachine, out nativeMachine)) { return nativeMachine; }
        } catch (EntryPointNotFoundException) { }
        return -1;
    }
    public static ulong[] Memory() {
        MemoryStatusEx status = new MemoryStatusEx();
        status.Length = (uint)Marshal.SizeOf(typeof(MemoryStatusEx));
        if (!GlobalMemoryStatusEx(ref status)) { return null; }
        return new ulong[] { status.TotalPhys, status.AvailPhys, status.TotalPageFile };
    }
}
"@
$HaveNative = $true
try { if (-not ('LlmProbeNative' -as [type])) { Add-Type -TypeDefinition $NativeCode -Language CSharp } }
catch { $HaveNative = $false }

# The caches of GetLogicalProcessorInformation (its own type: a failure here costs only CPU_L2_CACHE_KB). An entry is
# SYSTEM_LOGICAL_PROCESSOR_INFORMATION: ProcessorMask, Relationship (RelationCache = 2) and a 16-byte union whose
# CACHE_DESCRIPTOR has Level (byte 0), Size (bytes 4-7) and Type (bytes 8-11; CacheUnified = 0, CacheData = 2).
$CacheCode = @"
using System;
using System.Runtime.InteropServices;
public static class LlmProbeCache {
    [StructLayout(LayoutKind.Sequential)]
    public struct ProcessorInformation { public UIntPtr Mask; public int Relationship; public ulong Data0; public ulong Data1; }
    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    static extern bool GetLogicalProcessorInformation(IntPtr buffer, ref uint length);

    // KiB of the largest level-2 unified or data cache; 0 when none is listed, -1 when Windows cannot tell.
    public static long LargestL2Kb() {
        uint length = 0;
        GetLogicalProcessorInformation(IntPtr.Zero, ref length);
        int size = Marshal.SizeOf(typeof(ProcessorInformation));
        if (length < size) { return -1; }
        IntPtr buffer = Marshal.AllocHGlobal((int)length);
        try {
            if (!GetLogicalProcessorInformation(buffer, ref length)) { return -1; }
            long best = 0;
            for (long offset = 0; offset + size <= length; offset += size) {
                ProcessorInformation info = (ProcessorInformation)Marshal.PtrToStructure(
                    new IntPtr(buffer.ToInt64() + offset), typeof(ProcessorInformation));
                if (info.Relationship != 2) { continue; }
                long level = (long)(info.Data0 & 0xFF);
                long kb = (long)((info.Data0 >> 32) & 0xFFFFFFFF) / 1024;
                long type = (long)(info.Data1 & 0xFFFFFFFF);
                if (level == 2 && (type == 0 || type == 2) && kb > best) { best = kb; }
            }
            return best;
        } finally { Marshal.FreeHGlobal(buffer); }
    }
}
"@
$L2Kb = -1
try {
    if (-not ('LlmProbeCache' -as [type])) { Add-Type -TypeDefinition $CacheCode -Language CSharp }
    $L2Kb = [LlmProbeCache]::LargestL2Kb()
} catch { $L2Kb = -1 }

Write-Log "Hardware probe $ProbeVersion (schema 2)..."

# --- meta and system ---------------------------------------------------------------------------------------------
Put 'PROBE_SCHEMA' '2'
Put 'PROBE_TOOL' 'ps1'
Put 'PROBE_TOOL_VERSION' $ProbeVersion
Put 'GENERATED_AT' ((Get-Date).ToUniversalTime().ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", $Invariant))
Put 'OS' 'windows'

$version = [Environment]::OSVersion.Version
$build = [int]$version.Build
$versionText = "$($version.Major).$($version.Minor).$($version.Build)"
Put 'OS_ID' 'windows'
Put 'OS_FAMILY' 'windows'
Put 'OS_VERSION_ID' $versionText '^[A-Za-z0-9][A-Za-z0-9._~-]{0,31}$'
$product = Get-Reg 'SOFTWARE\Microsoft\Windows NT\CurrentVersion' 'ProductName'
if ($product) {
    # Windows 11 still writes "Windows 10" into ProductName.
    if ($build -ge 22000) { $product = ([string]$product).Replace('Windows 10', 'Windows 11') }
    Put-Text 'OS_PRETTY_NAME' $product
}
Put 'KERNEL' $versionText '^[A-Za-z0-9][A-Za-z0-9._+~-]{0,63}$'

$own = [string]$env:PROCESSOR_ARCHITEW6432
if ($own -eq '') { $own = [string]$env:PROCESSOR_ARCHITECTURE }
$ownArch = $ArchAliases[$own.ToLower()]
if (-not $ownArch) { $ownArch = 'other' }
$arch = $ownArch
$native = -1
if ($HaveNative) { try { $native = [LlmProbeNative]::NativeMachine() } catch { $native = -1 } }
if ($native -ge 0) {
    if ($Machines.ContainsKey($native)) { $arch = $Machines[$native] }
    if ($ownArch -ne 'other' -and $arch -ne $ownArch) { Add-Warning 'emulated' }
}
Put 'ARCH' $arch
$archClass = 'other'
if ($arch -eq 'x86_64' -or $arch -eq 'x86') { $archClass = 'x86' }
if ($arch -eq 'arm64' -or $arch -eq 'armv7' -or $arch -eq 'armv6') { $archClass = 'arm' }

Put 'LIBC' 'ucrt'
try {
    $ucrt = [Diagnostics.FileVersionInfo]::GetVersionInfo((Join-Path $env:SystemRoot 'System32\ucrtbase.dll'))
    Put 'LIBC_VERSION' "$($ucrt.FileMajorPart).$($ucrt.FileMinorPart).$($ucrt.FileBuildPart).$($ucrt.FilePrivatePart)" '^[0-9]{1,3}(\.[0-9]{1,5}){1,3}$' -Quiet
} catch { }
Put 'INIT_SYSTEM' 'scm'

# What the Windows API does not give directly: core count, the memory type and speed of the first module (no part
# or serial numbers) and the vendor:device ids of present PnP devices (no instance paths).
$cores = $null; $memoryType = $null; $memorySpeed = $null; $ids = @(); $intelNpu = $false
try {
    $processors = @(Get-CimInstance Win32_Processor)
    $cores = [int](($processors | Measure-Object NumberOfCores -Sum).Sum)
} catch { }
try {
    $modules = @(Get-CimInstance Win32_PhysicalMemory)
    if ($modules.Count -gt 0) { $memoryType = [int]$modules[0].SMBIOSMemoryType }
    $speeds = @($modules | ForEach-Object { if ($_.ConfiguredClockSpeed) { [int]$_.ConfiguredClockSpeed } elseif ($_.Speed) { [int]$_.Speed } })
    if ($speeds.Count -gt 0) { $memorySpeed = [int](($speeds | Measure-Object -Maximum).Maximum) }
} catch { }
try {
    $devices = @(Get-PnpDevice -PresentOnly -ErrorAction Stop)
    $ids = @($devices | ForEach-Object {
        if ($_.InstanceId -match '(VID|VEN)_([0-9A-Fa-f]{4})&(PID|DEV)_([0-9A-Fa-f]{4})') {
            $bus = 'pci'; if ($Matches[1] -eq 'VID') { $bus = 'usb' }
            ($bus + ':' + $Matches[2] + ':' + $Matches[4]).ToLower() } } | Sort-Object -Unique)
    $intelNpu = @($devices | Where-Object { $_.FriendlyName -like '*AI Boost*' }).Count -gt 0
} catch { }

# --- CPU -----------------------------------------------------------------------------------------------------------
$cpuKey = 'HARDWARE\DESCRIPTION\System\CentralProcessor\0'
$cpuName = Get-Reg $cpuKey 'ProcessorNameString'
if ($cpuName -and ([string]$cpuName).Trim() -ne '') { Put-Text 'CPU_MODEL' $cpuName } else { Add-Warning 'not_determined:CPU_MODEL' }
$vendorId = Get-Reg $cpuKey 'VendorIdentifier'
if ($vendorId -and $X86Vendors.ContainsKey(([string]$vendorId).Trim())) { Put 'CPU_VENDOR' $X86Vendors[([string]$vendorId).Trim()] }
Put 'CPU_CORES_LOGICAL' ([Environment]::ProcessorCount)
if ($cores) { Put 'CPU_CORES_PHYSICAL' $cores '^[1-9][0-9]{0,3}$' }
Put 'CPU_FLAGS_SOURCE' 'windows-api'
foreach ($key in $Features.Keys) {
    $constant, $minBuild, $group = $Features[$key]
    if ($group -ne $archClass) { Set-Flag $key 'OFF'; continue }
    if ($null -eq $constant) { Set-Flag $key 'UNKNOWN'; continue }
    $answer = $null
    if ($HaveNative) { try { $answer = [LlmProbeNative]::IsProcessorFeaturePresent([uint32]$constant) } catch { $answer = $null } }
    if ($answer -eq $true) { Set-Flag $key 'ON' }
    elseif ($answer -eq $false -and $build -ge $minBuild) { Set-Flag $key 'OFF' }
    else { Set-Flag $key 'UNKNOWN' }
}
if ($L2Kb -lt 0) { Add-Warning 'not_determined:CPU_L2_CACHE_KB' }
elseif ($L2Kb -gt 1048576) { Add-Warning 'not_determined:CPU_L2_CACHE_KB' }
elseif ($L2Kb -gt 0) { Put 'CPU_L2_CACHE_KB' ([string]$L2Kb) }

# --- memory ------------------------------------------------------------------------------------------------------
$memory = $null
if ($HaveNative) { try { $memory = [LlmProbeNative]::Memory() } catch { $memory = $null } }
if ($memory) {
    Put 'RAM_TOTAL_MB' ([math]::Floor($memory[0] / 1MB)) '^[0-9]{1,12}$'
    $swap = [double]$memory[2] - [double]$memory[0]
    if ($swap -lt 0) { $swap = 0 }
    Put 'SWAP_TOTAL_MB' ([math]::Floor($swap / 1MB)) '^[0-9]{1,12}$'
    Put 'RAM_AVAILABLE_MB' ([math]::Floor($memory[1] / 1MB)) '^[0-9]{1,12}$'
} else {
    try {
        $os = Get-CimInstance Win32_OperatingSystem
        Put 'RAM_TOTAL_MB' ([math]::Floor($os.TotalVisibleMemorySize / 1024)) '^[0-9]{1,12}$'
        Put 'RAM_AVAILABLE_MB' ([math]::Floor($os.FreePhysicalMemory / 1024)) '^[0-9]{1,12}$'
    } catch { }
}
if ($null -ne $memoryType -and $SmbiosTypes.ContainsKey($memoryType)) { Put 'RAM_TYPE' $SmbiosTypes[$memoryType] }
if ($memorySpeed -ge 100 -and $memorySpeed -le 20000) { Put 'RAM_SPEED_MTS' ([int]$memorySpeed) }
$diskPath = $null
$candidates = @($env:LLM_FRAMEWORK_HOME)
if ($env:USERPROFILE) { $candidates += @((Join-Path $env:USERPROFILE '.llm-framework'), $env:USERPROFILE) }
foreach ($candidate in $candidates) {
    if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Container)) { $diskPath = $candidate; break }
}
if ($diskPath) {
    try {
        $drive = New-Object System.IO.DriveInfo ([System.IO.Path]::GetPathRoot((Resolve-Path -LiteralPath $diskPath).Path))
        Put 'DISK_FREE_MB' ([math]::Floor($drive.AvailableFreeSpace / 1MB)) '^[0-9]{1,12}$' -Quiet
    } catch { }
}

# --- Hailo (HAILO_PLAN.md 2.4: the PnP ids and hailortcli on PATH, with the rules of hardware_probe.py) ----------
# Tables: copies of configs/hardware_probe_schema.yml (tests compare them). hailortcli runs with fixed arguments and an
# empty stdin: -v, scan, fw-control identify -s <id> for up to 16 devices (never --extended); of identify only the
# architecture and firmware lines are read. The Linux-only driver keys and the pip version are not written here.
$HailoArchNames = @{ 'HAILO8' = 'hailo8'; 'HAILO8_B0' = 'hailo8'; 'HAILO8L' = 'hailo8l'; 'HAILO10H' = 'hailo10h'; 'HAILO15H' = 'hailo15h'; 'HAILO15M' = 'hailo15m'; 'HAILO15L' = 'hailo15l'; 'HAILO12L' = 'hailo12l'; 'MARS' = 'hailo12l' }
$HailoArchFamily = @{ 'hailo8' = 'hailo8'; 'hailo8l' = 'hailo8'; 'hailo10h' = 'hailo1x'; 'hailo15h' = 'hailo1x'; 'hailo15m' = 'hailo1x'; 'hailo15l' = 'hailo1x'; 'hailo12l' = 'hailo1x' }
$HailoPciDevices = @{ '0x1e60:0x2864' = 'hailo8'; '0x1e60:0x45c4' = 'hailo1x'; '0x1e60:0x43a2' = 'hailo1x'; '0x1e60:0x26a2' = 'hailo1x' }
$HailoUsbDevices = @{ '0x0b05:0x1d6f' = 'hailo10h' }
$HailoIdPattern = '^(\[integrated\]|usb/[0-9]{3}:[0-9]{3}|(pci/)?([0-9a-fA-F]{4}:)?[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7])$'

function Hailo-Interface([string]$Id) {
    if ($Id -ceq '[integrated]') { return 'integrated' }
    if ($Id.StartsWith('usb/')) { return 'usb' }
    return 'pcie'
}

function Distinct-List($Items) {
    $out = New-Object System.Collections.Generic.List[string]
    foreach ($item in $Items) { if ($null -ne $item -and -not $out.Contains([string]$item)) { $out.Add([string]$item) } }
    return ,$out
}

$hailoFamilies = New-Object System.Collections.Generic.List[string]
$hailoPci = 0; $hailoUsb = 0; $hailoUsbArch = $null
foreach ($id in @($ids | Sort-Object)) {
    $parts = ([string]$id).Split(':')
    if ($parts.Count -ne 3) { continue }
    $pair = '0x' + $parts[1] + ':0x' + $parts[2]
    if ($parts[0] -eq 'pci' -and $parts[1] -eq '1e60') {
        $hailoPci++
        if ($HailoPciDevices.ContainsKey($pair)) { $hailoFamilies.Add($HailoPciDevices[$pair]) }
    }
    if ($parts[0] -eq 'usb' -and $HailoUsbDevices.ContainsKey($pair)) {
        $hailoUsb++
        $hailoUsbArch = $HailoUsbDevices[$pair]
        $hailoFamilies.Add($HailoArchFamily[$hailoUsbArch])
    }
}
$hailoInstalled = $false; $hailoWarn = $null; $hailoVersion = $null; $hailoIds = $null
$hailoRecords = New-Object System.Collections.ArrayList
$hailoClock = [Diagnostics.Stopwatch]::StartNew()
$result = Invoke-Tool 'hailortcli' @('-v')
if ($null -ne $result) {
    $hailoInstalled = $true
    # NUL bytes removed from every hailortcli output, as a shell drops them (hardware_probe.sh, .py)
    $text = ([string]$result.Out + "`n" + [string]$result.Err).Replace([string][char]0, '')
    $versionText = $null
    if ($text -cmatch 'HailoRT-CLI version ([0-9]+\.[0-9]+\.[0-9]+)') { $versionText = $Matches[1] }
    if ($text -cmatch 'libhailort version \(([0-9.]+)\) does not match HailoRT-CLI version \(([0-9.]+)\)') { $hailoWarn = 'version_mismatch' }
    elseif ($result.Code -ne 0) { if ($versionText) { $hailoWarn = 'not_determined' } else { $hailoWarn = 'version_mismatch' } }
    elseif ($versionText -and $versionText -cmatch $P_Dotted) { $hailoVersion = $versionText }
    else { $hailoWarn = 'not_determined' }
    if ($hailoWarn -ne 'version_mismatch') {
        $result = Invoke-Tool 'hailortcli' @('scan')
        if ($null -ne $result -and $result.Code -eq 0) {
            $scanOut = ([string]$result.Out).Replace([string][char]0, '')
            $found = @([regex]::Matches($scanOut, '(?m)^\s*\[-\]\s*Device:\s*(\S+)\s*$') | ForEach-Object { $_.Groups[1].Value })
            if ($found.Count -gt 0 -or $scanOut.Contains('Hailo devices not found')) {
                $hailoIds = New-Object System.Collections.Generic.List[string]
                foreach ($d in $found) { if ($d -cmatch $HailoIdPattern -and -not $hailoIds.Contains($d)) { $hailoIds.Add($d) } }
            }
        }
        if ($null -ne $hailoIds) {
            foreach ($d in @($hailoIds | Select-Object -First 16)) {
                if ($hailoClock.Elapsed.TotalSeconds -ge 60) { break }
                $result = Invoke-Tool 'hailortcli' @('fw-control', 'identify', '-s', $d)
                if ($null -eq $result) { break }   # it did not answer: no further device is asked
                if ($result.Code -ne 0) { continue }
                $clean = ([string]$result.Out).Replace([string][char]0, '')
                $word = $null; $fw = $null
                if ($clean -cmatch '(?m)^\s*Device Architecture\s*:\s*([A-Za-z0-9_]+)\s*$') { $word = $Matches[1].ToUpperInvariant() }
                if ($clean -cmatch '(?m)^\s*Firmware Version\s*:\s*([0-9][0-9.]*)') { $fw = $Matches[1]; if ($fw -cnotmatch $P_Dotted) { $fw = $null } }
                if ($word) { [void]$hailoRecords.Add(@($d, $word, $fw)) }
            }
        }
    }
}
$hailoPresent = ($hailoPci -gt 0) -or ($hailoUsb -gt 0) -or ($null -ne $hailoIds -and $hailoIds.Count -gt 0)
if ($hailoVersion) { Put 'HAILORT_VERSION' $hailoVersion $P_Dotted }
elseif ($hailoWarn -eq 'version_mismatch') { Add-Warning 'version_mismatch:HAILORT_VERSION' }
elseif ($hailoWarn) { Add-Warning 'not_determined:HAILORT_VERSION' }
$hailoArchs = New-Object System.Collections.Generic.List[string]
$hailoFws = New-Object System.Collections.Generic.List[string]
$hailoUnknown = $false
foreach ($record in $hailoRecords) {
    if (-not $HailoArchNames.ContainsKey($record[1])) { $hailoUnknown = $true; continue }
    # Not $arch: PowerShell names ignore case, so that would overwrite the script's ARCH value ($arch above).
    $recordArch = $HailoArchNames[$record[1]]
    if ($recordArch -eq 'hailo15h' -and (Hailo-Interface $record[0]) -ne 'integrated') { $recordArch = 'hailo10h' }
    $hailoArchs.Add($recordArch)
    if ($record[2]) { $hailoFws.Add($record[2]) }
}
$hailoArchs = Distinct-List $hailoArchs
$hailoFws = Distinct-List $hailoFws
$hailoArch = $null
if ($hailoArchs.Count -gt 1 -or $hailoUnknown) { Add-Warning 'not_determined:HAILO_ARCH' }
elseif ($hailoArchs.Count -eq 1) { $hailoArch = $hailoArchs[0] }
elseif ($hailoUsbArch -and $hailoPci -eq 0 -and $hailoRecords.Count -eq 0) { $hailoArch = $hailoUsbArch }
$families = @()
if ($hailoArch) { $families += $HailoArchFamily[$hailoArch] }
$families = Distinct-List ($families + @($hailoFamilies))
$hailoFamily = $null
if ($families.Count -eq 1) { $hailoFamily = $families[0] }
$hailoConflict = $families.Count -gt 1
if ($hailoConflict) { $hailoArch = $null; Add-Warning 'not_determined:HAILO_FAMILY' }
if ($hailoPresent -and -not $hailoArch -and -not $hailoConflict -and $hailoWarn -ne 'version_mismatch') { Add-Warning 'not_determined:HAILO_ARCH' }
if ($hailoFws.Count -eq 1) { Put 'HAILO_FW_VERSION' $hailoFws[0] $P_Dotted }
elseif ($hailoFws.Count -gt 1) { Add-Warning 'not_determined:HAILO_FW_VERSION' }
if ($null -ne $hailoIds -and $hailoIds.Count -gt 0) { $interfaces = Distinct-List @($hailoIds | ForEach-Object { Hailo-Interface $_ }) }
else {
    $interfaces = @()
    if ($hailoPci -gt 0) { $interfaces += 'pcie' }
    if ($hailoUsb -gt 0) { $interfaces += 'usb' }
}
if (@($interfaces).Count -eq 1) { Put 'HAILO_INTERFACE' @($interfaces)[0] }
elseif (@($interfaces).Count -gt 1) { Add-Warning 'not_determined:HAILO_INTERFACE' }
$hailoCount = $null
if ($null -ne $hailoIds -and $hailoIds.Count -gt 0) { $hailoCount = $hailoIds.Count }
elseif ($hailoPresent) { $hailoCount = $hailoPci + $hailoUsb }
elseif ($null -ne $hailoIds) { $hailoCount = 0 }
if ($null -ne $hailoCount) {
    if ($hailoCount -le 64) { Put 'HAILO_DEVICES' ([string]$hailoCount) } else { Add-Warning 'not_determined:HAILO_DEVICES' }
} elseif ($hailoInstalled -and $hailoWarn -ne 'version_mismatch') { Add-Warning 'not_determined:HAILO_DEVICES' }
if ($hailoFamily) { Put 'HAILO_FAMILY' $hailoFamily }
if ($hailoArch) { Put 'HAILO_ARCH' $hailoArch }
$hailoModel = $null
if ($hailoArch) { $hailoModel = 'hailo-' + $hailoArch.Substring(5) }

# --- NPU and USB-attached boards (a board attached over USB is not this machine's NPU) -------------------------
$pciVendors = @($ids | Where-Object { $_ -like 'pci:*' } | ForEach-Object { $_.Split(':')[1] })
$hailo = $hailoPresent
$metis = $pciVendors -contains '1f4b'
$mx3 = $pciVendors -contains '1d6b'
$coral = ($ids -contains 'pci:1ac1:089a') -or ($ids -contains 'usb:1a6e:089a') -or ($ids -contains 'usb:18d1:9302')
$npuVendor = $null; $npuModel = $null
if ($hailo) { $npuVendor = 'hailo'; $npuModel = $hailoModel }
elseif ($metis) { $npuVendor = 'axelera' }
elseif ($mx3) { $npuVendor = 'memryx' }
elseif ($coral) { $npuVendor = 'google' }
elseif ($intelNpu) { $npuVendor = 'intel'; $npuModel = 'intel-npu' }
$rockchip = @($ids | Where-Object { $_ -like 'usb:2207:*' } | Sort-Object)
if ($rockchip.Count -gt 0) {
    $usbPid = '0x' + $rockchip[0].Split(':')[2]
    Put 'USB_ROCKCHIP_PID' $usbPid '^0x[0-9a-f]{4}$'
    $mode = $RockchipModes[$usbPid]
    if (-not $mode) { $mode = 'other' }
    Put 'USB_ROCKCHIP_MODE' $mode
}
Set-Flag 'SUPPORTS_RKNN' 'OFF'
Set-Flag 'SUPPORTS_HAILO' $(if ($hailo) { 'ON' } else { 'OFF' })
Set-Flag 'SUPPORTS_METIS' $(if ($metis) { 'ON' } else { 'OFF' })
Set-Flag 'SUPPORTS_MX3' $(if ($mx3) { 'ON' } else { 'OFF' })
Set-Flag 'SUPPORTS_CORAL' $(if ($coral) { 'ON' } else { 'OFF' })
Set-Flag 'SUPPORTS_INTEL_NPU' $(if ($intelNpu) { 'ON' } else { 'OFF' })
if ($npuVendor) {
    Put 'NPU_VENDOR' $npuVendor
    Put 'NPU_MODEL' $npuModel '^[a-z0-9][a-z0-9._-]{0,31}$' -Quiet
}

# --- GPU -----------------------------------------------------------------------------------------------------------
function New-Gpu([string]$Vendor, [string]$Name, [string]$Source) {
    return [ordered]@{ VENDOR = $Vendor; NAME = $Name; PCI_ID = $null; DISCRETE = 'UNKNOWN'; UNIFIED = 'UNKNOWN';
                       VRAM_MB = $null; VRAM_FREE_MB = $null; DRIVER_VERSION = $null; COMPUTE_CAP = $null;
                       GFX_TARGET = $null; SOURCE = $Source }
}

function Get-Number([string]$Text) {
    $value = 0.0
    if ([double]::TryParse($Text.Trim(), [Globalization.NumberStyles]::Float, $Invariant, [ref]$value)) { return $value }
    return $null
}

function Guess-Discrete([string]$Vendor, [string]$Name) {
    if ($Vendor -eq 'amd') {
        if ($Name -match '(?i)Radeon(\s*\(TM\))?\s+Graphics|\b\d{3}M\b|\bVega\s+(3|6|7|8|9|10|11)\b|\b80[456]0S\b') { return $false }
        if ($Name -match '(?i)\b(RX|PRO|Instinct|FirePro|R9|R7)\b') { return $true }
        return $null
    }
    if ($Vendor -eq 'intel') { return [bool]($Name -match '(?i)\bArc\b.*\b[AB]\d{3}') }
    return $null
}

function Norm-Name([string]$Name) {
    return ((($Name -replace '(?i)\((TM|R)\)|[™®]', '') -replace '\s+', ' ').Trim()).ToLower()
}

# NVIDIA GPUs from nvidia-smi (older drivers install it outside PATH).
$nvidia = New-Object System.Collections.ArrayList
$smi = $null
$fields = 'driver_version,compute_cap,memory.total,memory.free,pci.bus_id,pci.device_id,name'
$oldFields = 'driver_version,memory.total,memory.free,pci.bus_id,pci.device_id,name'
$smiPaths = @('nvidia-smi')
foreach ($base in @($env:ProgramW6432, $env:ProgramFiles)) { if ($base) { $smiPaths += (Join-Path $base 'NVIDIA Corporation\NVSMI\nvidia-smi.exe') } }
foreach ($exe in $smiPaths) {
    $result = Invoke-Tool $exe @("--query-gpu=$fields", '--format=csv,noheader,nounits')
    if ($null -eq $result) { continue }
    $smi = $exe
    $out = [string]$result.Out
    if ($result.Code -ne 0 -and ((Tool-Text $result) -match 'compute_cap|pci\.')) {
        # Drivers before R510 do not know compute_cap: ask without it and leave the column empty.
        $result = Invoke-Tool $exe @("--query-gpu=$oldFields", '--format=csv,noheader,nounits')
        if ($null -eq $result) { break }
        $out = (([string]$result.Out -split "`n") | ForEach-Object { $_ -replace '^([^,]*),', '$1,,' }) -join "`n"
    }
    if ($result.Code -eq 0) {
        foreach ($line in ($out -split "`n")) {
            $parts = @($line.Split([char[]]',', 7) | ForEach-Object { $_.Trim() })
            if ($parts.Count -eq 7) { $driver, $cap, $total, $free, $bus, $device, $name = $parts }
            elseif ($parts.Count -ge 5) {
                $driver, $cap, $total, $free = $parts[0..3]; $name = ($parts[4..($parts.Count - 1)] -join ','); $device = ''
            } else { continue }
            if ($driver -eq '' -or $driver.StartsWith('[')) { continue }
            $gpuName = Clean-Text $name
            if ($gpuName -eq '') { $gpuName = 'NVIDIA GPU' }
            $gpu = New-Gpu 'nvidia' $gpuName 'nvidia-smi'
            $gpu.DISCRETE = 'ON'; $gpu.UNIFIED = 'OFF'
            if ($driver -cmatch $P_Version) { $gpu.DRIVER_VERSION = $driver }
            $capValue = Get-Number $cap
            if ($null -ne $capValue) {
                $capText = $capValue.ToString('0.0', $Invariant)
                if ($capText -match '^[0-9]{1,2}\.[0-9]$') { $gpu.COMPUTE_CAP = $capText }
            }
            $totalValue = Get-Number $total
            if ($totalValue) { $gpu.VRAM_MB = [string][math]::Floor($totalValue) }
            $freeValue = Get-Number $free
            if ($null -ne $freeValue -and $freeValue -ge 0) { $gpu.VRAM_FREE_MB = [string][math]::Floor($freeValue) }
            if ($device -match '^0x([0-9a-fA-F]{4})([0-9a-fA-F]{4})$') {
                $gpu.PCI_ID = ('0x' + $Matches[2] + ':0x' + $Matches[1]).ToLower()
            }
            [void]$nvidia.Add($gpu)
        }
    }
    break
}
if ($nvidia.Count -gt 0 -and $smi) {
    $header = Tool-Text (Invoke-Tool $smi @('-q', '-d', 'COMPUTE'))
    if ($header -match 'CUDA Version\s*:\s*([0-9]{1,2}\.[0-9]{1,3})\b') { Put 'NVIDIA_DRIVER_CUDA_MAX' $Matches[1] -Quiet }
}

# Vulkan devices (software renderers such as llvmpipe are skipped).
$vk = New-Object System.Collections.ArrayList
$vkRan = $false
$vkInstance = $null
$result = Invoke-Tool 'vulkaninfo' @('--summary') 20000
if ($null -ne $result -and $result.Code -eq 0 -and ([string]$result.Out) -match '(?m)^\s*Vulkan Instance Version:\s*([0-9][0-9.]*)') {
    $vkInstance = $Matches[1]
}
if ($null -ne $result -and $result.Code -eq 0 -and ([string]$result.Out).Contains('Devices')) {
    $vkRan = $true
    $devicesFound = New-Object System.Collections.ArrayList
    $current = $null
    foreach ($raw in (([string]$result.Out) -split "`n")) {
        $line = $raw.Trim()
        if ($line -match '^GPU[0-9]+:$') { $current = @{}; [void]$devicesFound.Add($current) }
        elseif ($null -ne $current -and $line.Contains(' = ')) {
            $i = $line.IndexOf(' = ')
            $current[$line.Substring(0, $i).Trim()] = $line.Substring($i + 3).Trim()
        }
    }
    foreach ($dev in $devicesFound) {
        $dtype = [string]$dev['deviceType']
        if ($dtype.EndsWith('_CPU') -or $dtype.EndsWith('_OTHER') -or -not $dev['deviceName']) { continue }
        $vendorId = $null; $deviceId = $null
        if ([string]$dev['vendorID'] -match '^\s*(0[xX])?([0-9a-fA-F]{1,8})\s*$') { $vendorId = '0x' + ([Convert]::ToInt64($Matches[2], 16)).ToString('x4') }
        if ([string]$dev['deviceID'] -match '^\s*(0[xX])?([0-9a-fA-F]{1,8})\s*$') { $deviceId = '0x' + ([Convert]::ToInt64($Matches[2], 16)).ToString('x4') }
        $vendor = 'other'
        if ($vendorId -and $PciVendors.ContainsKey($vendorId)) { $vendor = $PciVendors[$vendorId] }
        $gpuName = Clean-Text $dev['deviceName']
        if ($gpuName -eq '') { $gpuName = 'GPU' }
        $gpu = New-Gpu $vendor $gpuName 'vulkaninfo'
        $gpu.DISCRETE = $(if ($dtype.EndsWith('DISCRETE_GPU')) { 'ON' } else { 'OFF' })
        $gpu.UNIFIED = $(if ($dtype.EndsWith('INTEGRATED_GPU')) { 'ON' } else { 'OFF' })
        if ($vendorId -and $deviceId -and $deviceId -ne '0x0000' -and $vendorId.Length -eq 6 -and $deviceId.Length -eq 6) { $gpu.PCI_ID = "$($vendorId):$($deviceId)" }
        $driver = First-Number ([string]$dev['driverInfo'])
        if ($driver -and $driver -cmatch $P_Version) { $gpu.DRIVER_VERSION = $driver }
        [void]$vk.Add($gpu)
    }
}

# Display adapters: names from WMI, the exact dedicated memory from the display-class registry.
$adapters = New-Object System.Collections.ArrayList
try {
    $mem = @{}
    $display = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}\0*'
    Get-ItemProperty $display -ErrorAction SilentlyContinue | ForEach-Object {
        $v = $_.'HardwareInformation.qwMemorySize'
        if ($_.DriverDesc -and ($v -is [long] -or $v -is [int])) { $mem[$_.DriverDesc] = [int64]$v }
    }
    foreach ($controller in @(Get-CimInstance Win32_VideoController)) {
        $name = Clean-Text $controller.Name
        $pci = $null
        if ([string]$controller.PNPDeviceID -match 'VEN_([0-9A-Fa-f]{4})&DEV_([0-9A-Fa-f]{4})') { $pci = ('0x' + $Matches[1] + ':0x' + $Matches[2]).ToLower() }
        $maker = ("$($controller.AdapterCompatibility) $name").ToLower()
        $vendor = $null
        if ($pci) { $vendor = $PciVendors[$pci.Substring(0, 6)] }
        if (-not $vendor) {
            if ($maker.Contains('nvidia')) { $vendor = 'nvidia' }
            elseif ($maker.Contains('advanced micro') -or $maker.Contains('amd')) { $vendor = 'amd' }
            elseif ($maker.Contains('intel')) { $vendor = 'intel' }
            else { $vendor = 'other' }
        }
        if (@('nvidia', 'amd', 'intel') -notcontains $vendor -or $name -eq '') { continue }
        $discrete = Guess-Discrete $vendor $name
        if ($vendor -eq 'nvidia') { $discrete = $true }
        $gpu = New-Gpu $vendor $name 'wmi'
        if ($discrete -eq $true) { $gpu.DISCRETE = 'ON'; $gpu.UNIFIED = 'OFF' }
        elseif ($discrete -eq $false) { $gpu.DISCRETE = 'OFF'; $gpu.UNIFIED = 'ON' }
        $dedicated = $mem[$controller.Name]
        if ($null -ne $dedicated -and $dedicated -gt 0 -and $discrete -eq $true) { $gpu.VRAM_MB = [string][math]::Floor($dedicated / 1MB) }
        if ([string]$controller.DriverVersion -cmatch $P_Version) { $gpu.DRIVER_VERSION = [string]$controller.DriverVersion }
        if ($pci) { $gpu.PCI_ID = $pci }
        [void]$adapters.Add($gpu)
    }
} catch { }

$others = @($vk | Where-Object { $_.VENDOR -ne 'nvidia' -or $nvidia.Count -eq 0 })
$adapters = @($adapters | Where-Object { $_.VENDOR -ne 'nvidia' -or $nvidia.Count -eq 0 })
$vulkan = $false
# A vulkaninfo that ran and lists only software renderers (llvmpipe) means no GPU has a Vulkan driver: neither the
# adapters nor vulkan-1.dll make it Vulkan then.
if ($vk.Count -eq 0) {
    $others = $adapters
    $vulkan = (-not $vkRan) -and $adapters.Count -gt 0
} else {
    # vulkaninfo knows the device type, the registry the memory size.
    $byName = @{}
    foreach ($a in $adapters) { $byName[(Norm-Name $a.NAME)] = $a }
    foreach ($g in $others) {
        $match = $byName[(Norm-Name $g.NAME)]
        if ($match -and $match.VRAM_MB -and $g.UNIFIED -ne 'ON') { $g.VRAM_MB = $match.VRAM_MB }
        if ($match -and $match.PCI_ID -and -not $g.PCI_ID) { $g.PCI_ID = $match.PCI_ID }
    }
}
if (-not $vulkan -and -not $vkRan -and $env:SystemRoot) { $vulkan = Test-Path -LiteralPath (Join-Path $env:SystemRoot 'System32\vulkan-1.dll') -PathType Leaf }
if ($vkRan) { Put 'VULKAN_DEVICES' $vk.Count '^[0-9]{1,2}$' -Quiet }
# The instance version: vulkaninfo's line, else the file version of the loader vulkan-1.dll (X.Y.Z of X.Y.Z.W).
if (-not $vkInstance -and $env:SystemRoot) {
    try {
        $loader = Join-Path $env:SystemRoot 'System32\vulkan-1.dll'
        if (Test-Path -LiteralPath $loader -PathType Leaf) {
            $info = [Diagnostics.FileVersionInfo]::GetVersionInfo($loader)
            $vkInstance = "$($info.FileMajorPart).$($info.FileMinorPart).$($info.FileBuildPart)"
        }
    } catch { }
}
Put 'VULKAN_INSTANCE_VERSION' $vkInstance '^[0-9]{1,2}\.[0-9]{1,3}\.[0-9]{1,5}$' -Quiet

# OpenCL platforms of clinfo: each "Platform Version" line with the "Platform Name" line before it, as
# <name>:<OpenCL version> (the name lower case, (r)/(tm) removed, other characters one "-", at most 48 characters).
function OpenCL-Name([string]$Name) {
    $t = (Clean-Text $Name 4096).ToLowerInvariant().Replace('(r)', '').Replace('(tm)', '')
    $t = ($t -creplace '[^a-z0-9]+', '-').Trim('-')
    if ($t.Length -gt 48) { $t = $t.Substring(0, 48) }
    return $t.TrimEnd('-')
}
$openCl = New-Object System.Collections.Generic.List[string]
$result = Invoke-Tool 'clinfo'
if ($null -ne $result -and $result.Code -eq 0) {
    $platformName = $null
    foreach ($raw in (([string]$result.Out) -split "`n")) {
        $line = $raw.Trim(" `t`r".ToCharArray())
        if ($line -ceq 'Platform Name' -or $line -cmatch '^Platform Name[ \t]') {
            $platformName = $line.Substring(13).Trim(" `t`r".ToCharArray()); continue
        }
        if ($platformName -and $line -cmatch '^Platform Version[ \t]+OpenCL ([0-9]+\.[0-9]+)') {
            $item = (OpenCL-Name $platformName) + ':' + $Matches[1]
            if ($item -cmatch '^[a-z0-9]([a-z0-9-]{0,46}[a-z0-9])?:[0-9]{1,2}\.[0-9]{1,2}$' -and -not $openCl.Contains($item) -and $openCl.Count -lt 16) {
                $openCl.Add($item)
            }
        }
    }
}
if ($openCl.Count -gt 0) { Put 'OPENCL_PLATFORMS' ($openCl -join ',') }

$gpus = @(@($nvidia) + @($others) | Select-Object -First 16)
Put 'GPU_COUNT' $gpus.Count
$index = 0
foreach ($g in $gpus) {
    Put-Gpu $index 'VENDOR' $g.VENDOR
    Put-Gpu $index 'NAME' $g.NAME
    Put-Gpu $index 'PCI_ID' $g.PCI_ID '^0x[0-9a-f]{4}:0x[0-9a-f]{4}$'
    Put-Gpu $index 'DISCRETE' $g.DISCRETE
    Put-Gpu $index 'UNIFIED' $g.UNIFIED
    if ($g.VRAM_MB -and [double]$g.VRAM_MB -le 16777216) { Put-Gpu $index 'VRAM_MB' $g.VRAM_MB '^[0-9]{1,8}$' }
    if ($g.VRAM_FREE_MB -and [double]$g.VRAM_FREE_MB -le 16777216) { Put-Gpu $index 'VRAM_FREE_MB' $g.VRAM_FREE_MB '^[0-9]{1,8}$' }
    Put-Gpu $index 'DRIVER_VERSION' $g.DRIVER_VERSION $P_Version
    if ($g.VENDOR -eq 'nvidia') { Put-Gpu $index 'COMPUTE_CAP' $g.COMPUTE_CAP '^[0-9]{1,2}\.[0-9]$' }
    if ($g.VENDOR -eq 'amd') { Put-Gpu $index 'GFX_TARGET' $g.GFX_TARGET '^gfx[0-9a-f]{3,5}$' }
    Put-Gpu $index 'SOURCE' $g.SOURCE
    $index++
}
Set-Flag 'SUPPORTS_CUDA' $(if (@($gpus | Where-Object { $_.VENDOR -eq 'nvidia' }).Count -gt 0) { 'ON' } else { 'OFF' })
Set-Flag 'SUPPORTS_INTEL_XPU' $(if (@($gpus | Where-Object { $_.VENDOR -eq 'intel' }).Count -gt 0) { 'ON' } else { 'OFF' })
Set-Flag 'VULKAN' $(if ($vulkan -or $vk.Count -gt 0) { 'ON' } else { 'OFF' })
Set-Flag 'HIP_RUNTIME' 'OFF'
Set-Flag 'METAL' 'OFF'

# --- toolchain: versions only, never paths ----------------------------------------------------------------------
if (-not $NoToolchain) {
    foreach ($tool in @(@('TOOL_CMAKE', 'cmake'), @('TOOL_NINJA', 'ninja'), @('TOOL_GIT', 'git'), @('TOOL_CCACHE', 'ccache'), @('TOOL_DOCKER', 'docker'))) {
        $text = Tool-Text (Invoke-Tool $tool[1] @('--version'))
        if ($null -ne $text) { Put $tool[0] (First-Number (First-Line $text)) $P_Version -Quiet }
    }
    $text = Tool-Text (Invoke-Tool 'nvcc' @('--version'))
    if ($text -match 'release \d+\.\d+, V(\d+(\.\d+){1,3})') { Put 'TOOL_NVCC' $Matches[1] $P_Version -Quiet }
    $text = Tool-Text (Invoke-Tool 'glslc' @('--version'))
    if ($null -ne $text) {
        $line = @(($text -split "`n") | Where-Object { $_ -match 'shaderc' })
        if ($line.Count -gt 0) { $line = $line[0] } else { $line = First-Line $text }
        Put 'TOOL_GLSLC' (First-Number $line) $P_Version -Quiet
    }
    $clangCl = Tool-Text (Invoke-Tool 'clang-cl' @('--version'))
    $msvc = Tool-Text (Invoke-Tool 'cl')
    $cc = $null
    if ($clangCl -match 'clang version (\d+(\.\d+){0,3})') { $cc = 'clang-' + $Matches[1]; Put 'TOOL_CLANG' $Matches[1] $P_Version -Quiet }
    if ($msvc -match 'Version (\d+(\.\d+){1,3})') {
        Put 'TOOL_MSVC' $Matches[1] $P_Version -Quiet
        if (-not $cc) { $cc = 'msvc-' + $Matches[1] }
    }
    if ($cc) {
        Put 'TOOL_CC' $cc '^(gcc|clang|appleclang|msvc|icx)-[0-9]{1,4}(\.[0-9]{1,6}){0,3}$' -Quiet
        Put 'TOOL_CXX' $cc '^(gcc|clang|appleclang|msvc|icx)-[0-9]{1,4}(\.[0-9]{1,6}){0,3}$' -Quiet
    }
    $text = Tool-Text (Invoke-Tool 'python' @('--version'))
    if ($text -match '(?m)^Python (\d+(\.\d+){1,3})') { Put 'TOOL_PYTHON' $Matches[1] $P_Version -Quiet }
}

# --- access: SSH server, sudo, ssh-keygen, package manager (probe 3.3.0; the rules of hardware_probe.py) ------
# Fixed words only: no path, host name or user name reaches the result. Windows elevates through UAC: SUDO=other.
$sshdService = Get-Service -Name 'sshd' -ErrorAction SilentlyContinue
$sshdExe = Join-Path $env:SystemRoot 'System32\OpenSSH\sshd.exe'
if ($sshdService) {
    Put 'SSH_SERVER' 'openssh'
    if ([string]$sshdService.Status -eq 'Running') { Put 'SSH_SERVER_SOURCE' 'process' } else { Put 'SSH_SERVER_SOURCE' 'service' }
    Put 'SSH_SERVERS_INSTALLED' 'openssh'
} elseif (Test-Path -LiteralPath $sshdExe) {
    Put 'SSH_SERVER' 'openssh'
    Put 'SSH_SERVER_SOURCE' 'binary'
    Put 'SSH_SERVERS_INSTALLED' 'openssh'
} else {
    Put 'SSH_SERVER' 'none'
}
Put 'SUDO' 'other'
if (Get-Command 'ssh-keygen' -CommandType Application -ErrorAction SilentlyContinue) {
    Put 'SSH_KEYGEN' 'openssh'
    $text = Tool-Text (Invoke-Tool 'ssh' @('-V'))
    if ($text -match 'OpenSSH_(?:for_Windows_)?([0-9]{1,2}\.[0-9]{1,2})') { Put 'SSH_KEYGEN_VERSION' $Matches[1] $P_Version }
    else { Add-Warning 'not_determined:SSH_KEYGEN_VERSION' }
} else {
    Put 'SSH_KEYGEN' 'none'
}
Put 'PACKAGE_MANAGER' 'other'

# --- the result ----------------------------------------------------------------------------------------------------
if ($script:Warnings.Count -gt 0) { Put 'PROBE_WARNINGS' ($script:Warnings -join ',') }
$missing = @('KERNEL', 'ARCH', 'CPU_CORES_LOGICAL', 'RAM_TOTAL_MB') | Where-Object { -not $script:Written.ContainsKey($_) }
$content = "# LLM Cross-Compiler Framework hardware profile (schema 2, hardware_probe.ps1 $ProbeVersion)`n" + (($script:Lines -join "`n") + "`n")
if ($OutputFile -eq '-') {
    [Console]::Out.Write($content)
} else {
    try { [IO.File]::WriteAllText($OutputFile, $content, (New-Object System.Text.UTF8Encoding($false))) }
    catch { Write-Log "Cannot write $($OutputFile): $($_.Exception.Message)"; Exit 1 }
    Write-Log "Wrote $OutputFile."
}
if (@($missing).Count -gt 0) { Write-Log ("Could not determine required keys: " + (@($missing) -join ', ')); Exit 1 }
Exit 0
