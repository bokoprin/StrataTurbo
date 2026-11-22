#!/usr/bin/env python3
"""
Update strata profile JSON and systemd service safely from baseline,
restart strata-llm.service, wait for /health loaded=true, and print effective engine cmdline.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

BASELINE_PROFILE = "/home/shinsuke/StrataTurbo/backups/strata-ud-iq3_xxs.json.baseline"
TARGET_PROFILE = "/home/shinsuke/strata-data/config/strata-ud-iq3_xxs.json"
BASELINE_SERVICE = "/home/shinsuke/StrataTurbo/backups/strata-llm.service.baseline"
TARGET_SERVICE = "/home/shinsuke/.config/systemd/user/strata-llm.service"
HEALTH_URL = "http://127.0.0.1:18080/health"


def set_flag(args_list, flag, value=None):
    """Remove existing flag (and its value if any), then append flag [value]."""
    out = []
    i = 0
    # Flags that take 1 argument
    valued_flags = {
        "--pack", "--native", "--ple-gguf", "--expert-profile", "--expert-cache",
        "--prefill", "--spec", "--max-context", "--kv", "--kv-resident",
        "--vram-reserve-mib", "--pool-workers", "--pool-affinity", "--aux-cpus",
        "--adapt-min-gain", "--draft-cap", "--draft-conf", "--lookup-chain",
        "--lookup-max-t", "--ple-io", "--draft-model", "--draft", "--draft-fallback",
        "--mtp-draft-vocab", "--spec-min-p",
    }
    while i < len(args_list):
        if args_list[i] == flag:
            if flag in valued_flags and i + 1 < len(args_list) and not args_list[i + 1].startswith("--"):
                i += 2
            else:
                i += 1
        else:
            out.append(args_list[i])
            i += 1
    out.append(flag)
    if value is not None and value != "":
        out.append(str(value))
    return out


def remove_flag(args_list, flag):
    out = []
    i = 0
    valued_flags = {
        "--pack", "--native", "--ple-gguf", "--expert-profile", "--expert-cache",
        "--prefill", "--spec", "--max-context", "--kv", "--kv-resident",
        "--vram-reserve-mib", "--pool-workers", "--pool-affinity", "--aux-cpus",
        "--adapt-min-gain", "--draft-cap", "--draft-conf", "--lookup-chain",
        "--lookup-max-t", "--ple-io",
    }
    while i < len(args_list):
        if args_list[i] == flag:
            if flag in valued_flags and i + 1 < len(args_list) and not args_list[i + 1].startswith("--"):
                i += 2
            else:
                i += 1
        else:
            out.append(args_list[i])
            i += 1
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--set", action="append", default=[], help="KEY=VAL or FLAG (e.g. --vram-reserve-mib=512 or --kv-grow)")
    parser.add_argument("--unset", action="append", default=[], help="Flag to remove")
    parser.add_argument("--env", action="append", default=[], help="Systemd Environment=KEY=VAL")
    parser.add_argument("--exe", default=None, help="Override profile exe path")
    parser.add_argument("--fix-vision", action="store_true", help="Update vision.exe to 0.1.64")
    args = parser.parse_args()

    with open(BASELINE_PROFILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    if args.exe:
        cfg["exe"] = args.exe
    if args.fix_vision and "vision" in cfg and isinstance(cfg["vision"], dict):
        vexe = cfg["vision"].get("exe", "")
        if "0.1.59-linux-x86_64-cuda-cuda13" in vexe:
            new_vexe = vexe.replace("0.1.59-linux-x86_64-cuda-cuda13", "0.1.64-linux-x86_64-cuda-cuda13")
            if os.path.exists(new_vexe):
                cfg["vision"]["exe"] = new_vexe

    cur_args = list(cfg.get("args", []))
    cur_user = list(cfg.get("user_args", []))

    for u in args.unset:
        cur_args = remove_flag(cur_args, u)
        cur_user = remove_flag(cur_user, u)

    for s in args.set:
        if "=" in s:
            k, v = s.split("=", 1)
            cur_args = set_flag(cur_args, k, v)
            cur_user = set_flag(cur_user, k, v)
        else:
            cur_args = set_flag(cur_args, s, None)
            cur_user = set_flag(cur_user, s, None)

    cfg["args"] = cur_args
    cfg["user_args"] = cur_user

    with open(TARGET_PROFILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")

    with open(BASELINE_SERVICE, "r", encoding="utf-8") as f:
        svc = f.read()

    if args.env:
        env_lines = "\n".join(f"Environment={e}" for e in args.env)
        svc = svc.replace(
            "Environment=STRATA_ENGINE_BACKEND=cuda",
            f"Environment=STRATA_ENGINE_BACKEND=cuda\n{env_lines}",
        )

    with open(TARGET_SERVICE, "w", encoding="utf-8") as f:
        f.write(svc)

    subprocess.check_call(["systemctl", "--user", "daemon-reload"])
    subprocess.check_call(["systemctl", "--user", "restart", "strata-llm.service"])

    for i in range(90):
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=3) as resp:
                h = json.loads(resp.read().decode("utf-8"))
                if h.get("loaded"):
                    print(f"Healthy after {i+1}s")
                    break
        except Exception:
            pass
        time.sleep(1)
    else:
        print("ERROR: service did not become healthy within 90s", file=sys.stderr)
        sys.exit(1)

    ps_out = subprocess.check_output("ps aux | grep '/strata --serve' | grep -v grep || true", shell=True, text=True)
    print("Effective cmdline:", ps_out.strip())


if __name__ == "__main__":
    main()
