<#
.SYNOPSIS
    LLM Framework Hardware Analyzer (Enterprise Edition - Windows v2.5)
    
.DESCRIPTION
    Performs a deep scan of system hardware (SoC, CPU Caches, RAM Speed/Timings, Accelerators).
    Generates 'target_hardware_config.txt'.
    
    HISTORY:
    v2.5.0: Added SoC detection via WMI/Registry, Deep RAM PartNumbers.
    v2.4.0: Added Deep RAM Scan (Speed per DIMM), L2/L3 Cache Info.
    v2.3.0: Strict VID/PID extraction.
#>

# 1. ADMIN CHECK
$currentPrincipal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Warning "Deep Scan requires Administrator privileges for SoC/RAM timings."
    try { Start-Process PowerShell -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""; Exit }
    catch { Write-Error "Failed to elevate."; Exit 1 }
}

Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope Process -Force -ErrorAction SilentlyContinue
$ErrorActionPreference = "Stop"
$OutputFile = "target_hardware_config.txt"

function Log-Output {
    param ([string]$Key, [string]$Value)
    $line = "$Key=$Value"
    Write-Host "[$((Get-Date).ToString('HH:mm:ss'))] $line"
    Add-Content -Path $OutputFile -Value $line
}

function Get-RegistryValue {
    param ($Path, $Name)
    try { return (Get-ItemProperty -Path $Path -Name $Name -ErrorAction SilentlyContinue).$Name } catch { return $null }
}

function Get-DeviceIdPart {
    param ([string]$InstanceId, [string]$Type)
    if ($InstanceId -match "$Type`_([0-9A-F]{4})") { return $Matches[1] }
    return "Unknown"
}

# === INITIALIZATION ===
Write-Host "=== LLM Framework Hardware Probe v2.5 (Windows Enterprise) ===" -ForegroundColor Cyan
New-Item -Path $OutputFile -ItemType File -Force | Out-Null
Log-Output "GENERATED_AT" "$(Get-Date)"
Log-Output "PLATFORM" "Windows"
Log-Output "HOSTNAME" $env:COMPUTERNAME
Log-Output "OS_VERSION" "$([System.Environment]::OSVersion.Version)"

# === 1. NATIVE API BRIDGE (C#) ===
# For CPU Flags not exposed via WMI
$Kernel32Code = @"
using System;
using System.Runtime.InteropServices;
public class HardwareInfo {
    [DllImport("kernel32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool IsProcessorFeaturePresent(uint ProcessorFeature);
    public const uint PF_ARM_NEON_INSTRUCTIONS_AVAILABLE = 37;
    public const uint PF_ARM_V8_INSTRUCTIONS_AVAILABLE = 29;
    public const uint PF_AVX_INSTRUCTIONS_AVAILABLE = 27;
    public const uint PF_AVX2_INSTRUCTIONS_AVAILABLE = 39;
    public const uint PF_AVX512F_INSTRUCTIONS_AVAILABLE = 40;
}
"@
try { Add-Type -TypeDefinition $Kernel32Code -Language CSharp } catch { Write-Warning "Native Bridge failed." }

# === 2. SYSTEM DEEP DIVE (SoC & CPU) ===

Write-Host "Analyzing SoC / CPU..." -ForegroundColor Green
try {
    $cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
    $sys = Get-CimInstance Win32_ComputerSystem
    
    # SoC Detection Logic
    $socName = "Unknown"
    
    # 1. Check Model Name (often contains SoC name on Windows on Arm)
    if ($cpu.Name -match "Snapdragon|Qualcomm|SQ1|SQ2|SQ3|Ampere") {
        $socName = $cpu.Name
    } 
    # 2. Fallback to System Model (often "Surface Pro X" implies SoC)
    elseif ($sys.Model -match "Surface Pro X") { $socName = "Microsoft SQ1/SQ2" }
    elseif ($sys.Model -match "Volterra") { $socName = "Snapdragon 8cx Gen 3" }
    else { $socName = "Discrete_CPU" } # Intel/AMD standard

    if ($socName -ne "Discrete_CPU") {
        Log-Output "SOC_VENDOR" "Qualcomm/ARM" # Assumption for Windows SoCs mostly
        Log-Output "SOC_MODEL" $socName
        Log-Output "CPU_ARCH_TYPE" "ARM64"
    } else {
        Log-Output "SOC_VENDOR" "None"
        Log-Output "CPU_ARCH_TYPE" "x86_64"
    }

    Log-Output "CPU_MODEL" $cpu.Name.Trim()
    Log-Output "CPU_CORES_LOGICAL" $cpu.NumberOfLogicalProcessors
    Log-Output "CPU_CORES_PHYSICAL" $cpu.NumberOfCores
    Log-Output "CPU_DEVICE_ID" "$($cpu.ProcessorId)_REV$($cpu.Revision)"
    
    # Caches (KB)
    Log-Output "CPU_L2_CACHE_SIZE_KB" $cpu.L2CacheSize
    Log-Output "CPU_L3_CACHE_SIZE_KB" $cpu.L3CacheSize
} catch { Log-Output "CPU_INFO" "Partial/Error" }

# Instruction Sets (Native Check)
$hasAvx2 = [HardwareInfo]::IsProcessorFeaturePresent([HardwareInfo]::PF_AVX2_INSTRUCTIONS_AVAILABLE)
$hasAvx512 = [HardwareInfo]::IsProcessorFeaturePresent([HardwareInfo]::PF_AVX512F_INSTRUCTIONS_AVAILABLE)
$hasNeon = [HardwareInfo]::IsProcessorFeaturePresent([HardwareInfo]::PF_ARM_NEON_INSTRUCTIONS_AVAILABLE)

Log-Output "SUPPORTS_AVX2" $hasAvx2.ToString().ToUpper()
Log-Output "SUPPORTS_AVX512" $hasAvx512.ToString().ToUpper()
Log-Output "SUPPORTS_NEON" $hasNeon.ToString().ToUpper()

# --- MEMORY DEEP DIVE (Timings & Modules) ---
Write-Host "Analyzing Memory Subsystem..." -ForegroundColor Green
try {
    $mems = Get-CimInstance Win32_PhysicalMemory
    $totalMem = 0
    $speeds = @()
    
    $slotIndex = 0
    foreach ($m in $mems) {
        $totalMem += $m.Capacity
        $speed = if ($m.ConfiguredClockSpeed) { $m.ConfiguredClockSpeed } else { $m.Speed }
        if ($speed) { $speeds += $speed }
        
        # Log individual stick info for AI analysis
        $part = $m.PartNumber.Trim()
        Log-Output "RAM_SLOT_${slotIndex}_PART" $part
        Log-Output "RAM_SLOT_${slotIndex}_SPEED" $speed
        $slotIndex++
    }
    
    Log-Output "RAM_TOTAL_MB" ([math]::Round($totalMem / 1MB))
    
    if ($speeds.Count -gt 0) {
        $maxSpeed = ($speeds | Measure-Object -Maximum).Maximum
        Log-Output "RAM_SPEED_MTS" $maxSpeed
    } else {
        Log-Output "RAM_SPEED_MTS" "Unknown"
    }
    Log-Output "RAM_MODULES_COUNT" $mems.Count
    
    # Detect Type via SMBIOS (approx)
    # 26=DDR4, 30=LPDDR4, 34=DDR5, 35=LPDDR5
    $memType = $mems[0].SMBIOSMemoryType
    switch ($memType) {
        26 { Log-Output "RAM_TYPE" "DDR4" }
        30 { Log-Output "RAM_TYPE" "LPDDR4" }
        34 { Log-Output "RAM_TYPE" "DDR5" }
        35 { Log-Output "RAM_TYPE" "LPDDR5" }
        Default { Log-Output "RAM_TYPE" "Unknown_($memType)" }
    }

} catch { Log-Output "RAM_DETAILS" "Error reading CIM" }

# === 3. ACCELERATOR DETECTION (Strict ID) ===
Write-Host "Scanning Accelerators..." -ForegroundColor Green
$npuFound = $false

# A. NVIDIA
$nvidia = Get-PnpDevice -PresentOnly -Class Display | Where-Object { $_.InstanceId -match "VEN_10DE" }
if ($nvidia) {
    $devId = Get-DeviceIdPart $nvidia.InstanceId "DEV"
    $venId = Get-DeviceIdPart $nvidia.InstanceId "VEN"
    Log-Output "GPU_VENDOR" "NVIDIA"
    Log-Output "GPU_VENDOR_ID" "0x$venId"
    Log-Output "GPU_DEVICE_ID" "0x$devId"
    Log-Output "SUPPORTS_CUDA" "ON"
    
    if (Get-Command "nvidia-smi" -ErrorAction SilentlyContinue) {
        $vram = nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits
        Log-Output "GPU_VRAM_MB" $vram
        Log-Output "GPU_DRIVER_VERSION" (nvidia-smi --query-gpu=driver_version --format=csv,noheader)
    }
} else { Log-Output "SUPPORTS_CUDA" "OFF" }

# B. Rockchip (USB via Windows ADB/Driver)
# Detection on Windows is purely VID/PID based as we can't access /dev/rknpu
$rkDev = Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -match "VID_2207" }
if ($rkDev) {
    $pid = Get-DeviceIdPart $rkDev.InstanceId "PID"
    Log-Output "NPU_VENDOR" "Rockchip"
    Log-Output "NPU_DEVICE_ID" "0x$pid"
    switch ($pid) {
        "350A" { Log-Output "NPU_MODE" "Maskrom (RK3588)" }
        "350B" { Log-Output "NPU_MODE" "Loader (RK3588)" }
        "0006" { Log-Output "NPU_MODE" "ADB/MTP" }
        "0019" { Log-Output "NPU_MODE" "ADB" }
        Default { Log-Output "NPU_MODE" "Unknown" }
    }
    Log-Output "SUPPORTS_RKNN" "ON" # Assuming toolkit is installed if device is present
    $npuFound = $true
}

# C. MemryX / Axelera / Hailo
function Check-Device ($Name, $VidSearch, $Flag) {
    $dev = Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -match $VidSearch -or $_.FriendlyName -like "*$Name*" }
    if ($dev) {
        # Try extract VID/PID if standard format
        if ($dev.InstanceId -match "VID_([0-9A-F]{4})&PID_([0-9A-F]{4})") {
            Log-Output "NPU_VENDOR_ID" "0x$($Matches[1])"
            Log-Output "NPU_DEVICE_ID" "0x$($Matches[2])"
        } elseif ($dev.InstanceId -match "VEN_([0-9A-F]{4})&DEV_([0-9A-F]{4})") {
            Log-Output "NPU_VENDOR_ID" "0x$($Matches[1])"
            Log-Output "NPU_DEVICE_ID" "0x$($Matches[2])"
        }
        Log-Output "NPU_VENDOR" $Name
        Log-Output $Flag "ON"
        return $true
    }
    return $false
}

if (Check-Device "MemryX" "VID_1D6B" "SUPPORTS_MX3") { $npuFound = $true }
if (Check-Device "Axelera" "VEN_1F4B" "SUPPORTS_METIS") { $npuFound = $true }
if (Check-Device "Hailo" "VEN_1E60" "SUPPORTS_HAILO") { $npuFound = $true }

# D. Intel NPU (Meteor Lake / Arrow Lake)
if (Check-Device "Intel(R) AI Boost" "VEN_8086" "SUPPORTS_INTEL_NPU") { 
    $npuFound = $true 
    Log-Output "NPU_MODEL" "Intel AI Boost (NPU)"
}

if (-not $npuFound) { Log-Output "NPU_STATUS" "None" }

# --- 4. SOFTWARE ---
if (Get-Command docker -ErrorAction SilentlyContinue) {
    Log-Output "DOCKER_VERSION" ((docker --version) -replace "Docker version ", "" -replace ",", "")
} else { Log-Output "DOCKER_VERSION" "Missing" }

Write-Host "✅ Deep Scan complete." -ForegroundColor Green
