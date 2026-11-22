#!/usr/bin/env bash
# Revert Phase 2 OS / Hardware tuning back to baseline defaults (requires sudo)
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "ERROR: Please run with sudo: sudo bash $0" >&2
  exit 1
fi

echo "=== 1. GPU Persistence Mode off & Power Limit -> 310W ==="
nvidia-smi -pm 0 || true
nvidia-smi -pl 310 || true

echo "=== 2. CPU Scaling Governor -> powersave ==="
for gov in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
  [[ -w "$gov" ]] && echo powersave > "$gov"
done

echo "=== 3. CPU Turbo -> disabled (no_turbo=1) ==="
if [[ -w /sys/devices/system/cpu/intel_pstate/no_turbo ]]; then
  echo 1 > /sys/devices/system/cpu/intel_pstate/no_turbo || true
fi

echo "=== 4. Release Static HugePages -> 0 ==="
sysctl -w vm.nr_hugepages=0
rm -f /etc/sysctl.d/99-strata-hugepages.conf

echo "OS tuning reverted to baseline defaults."
