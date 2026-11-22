#!/usr/bin/env bash
# Phase 2 OS / Hardware tuning script for StrataTurbo (requires sudo on gwol-target)
# Usage:
#   sudo bash apply_os_tuning.sh [--with-hugepages] [--with-turbo] [--persist]
#   Default (no flags): applies GPU Persistence Mode + 320W Power Limit + CPU performance governor.
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "ERROR: Please run with sudo: sudo bash $0 [--with-hugepages] [--with-turbo] [--persist]" >&2
  exit 1
fi

WITH_HUGEPAGES=0
WITH_TURBO=0
PERSIST=0

for arg in "$@"; do
  case "$arg" in
    --with-hugepages) WITH_HUGEPAGES=1 ;;
    --with-turbo) WITH_TURBO=1 ;;
    --persist) PERSIST=1 ;;
    --all) WITH_HUGEPAGES=1; WITH_TURBO=1 ;;
    *) echo "Unknown option: $arg" >&2; exit 1 ;;
  esac
done

echo "=== 1. GPU Persistence Mode & Power Limit (320W) ==="
nvidia-smi -pm 1
nvidia-smi -pl 320 || echo "WARN: Failed to set power limit to 320W (ignored)"
nvidia-smi --query-gpu=name,persistence_mode,power.limit,temperature.gpu --format=csv

echo ""
echo "=== 2. CPU Scaling Governor -> performance ==="
for gov in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
  [[ -w "$gov" ]] && echo performance > "$gov"
done
echo "cpu0 governor: $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || echo N/A)"

echo ""
echo "=== 3. CPU Turbo Boost Check ==="
NO_TURBO_PATH="/sys/devices/system/cpu/intel_pstate/no_turbo"
CPU0_MAX=$(cat /sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq 2>/dev/null || echo 0)
if [[ "$WITH_TURBO" -eq 1 ]]; then
  if [[ -w "$NO_TURBO_PATH" ]]; then
    echo 0 > "$NO_TURBO_PATH" || echo "WARN: Could not write 0 to $NO_TURBO_PATH"
    CUR_NO_TURBO=$(cat "$NO_TURBO_PATH")
    NEW_MAX=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq 2>/dev/null || echo 0)
    echo "no_turbo=$CUR_NO_TURBO, cpu0 scaling_max_freq=${NEW_MAX} kHz (cpuinfo_max_freq=${CPU0_MAX} kHz)"
    if [[ "$CUR_NO_TURBO" -eq 1 || "$NEW_MAX" -le 3200000 ]]; then
      echo "NOTE: Turbo appears locked at BIOS level (cpuinfo_max_freq=${CPU0_MAX} kHz)."
      echo "      To enable Turbo safely on i9-14900KF, update BIOS (microcode >= 0x129/0x12B) and enable Turbo with Intel Default Power Limits."
    fi
  else
    echo "WARN: $NO_TURBO_PATH not writable."
  fi
else
  echo "Skipped (--with-turbo not passed). Current no_turbo=$(cat "$NO_TURBO_PATH" 2>/dev/null || echo N/A), cpuinfo_max_freq=${CPU0_MAX} kHz."
fi

echo ""
echo "=== 4. Static HugePages (48 GiB = 24576 x 2 MiB) ==="
if [[ "$WITH_HUGEPAGES" -eq 1 ]]; then
  if pgrep -f "/strata --serve" >/dev/null 2>&1; then
    echo "ERROR: strata is currently running. Stop strata-llm.service first so RAM is free before allocating HugePages:" >&2
    echo "  systemctl --user stop strata-llm.service" >&2
    echo "  sudo bash $0 --with-hugepages" >&2
    echo "  systemctl --user start strata-llm.service" >&2
    exit 1
  fi
  sysctl -w vm.nr_hugepages=24576
  ALLOC=$(awk '/HugePages_Total/ {print $2}' /proc/meminfo)
  echo "HugePages_Total allocated: ${ALLOC} / 24576"
  if [[ "$PERSIST" -eq 1 ]]; then
    echo "vm.nr_hugepages = 24576" > /etc/sysctl.d/99-strata-hugepages.conf
    echo "Persisted to /etc/sysctl.d/99-strata-hugepages.conf"
  fi
else
  echo "Skipped (--with-hugepages not passed). Current vm.nr_hugepages=$(sysctl -n vm.nr_hugepages)."
fi

echo ""
echo "Done."
