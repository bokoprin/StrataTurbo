#!/usr/bin/env python3
"""
StrataTurbo automated benchmark & quality gate runner.
Runs directly on the GPU server against http://127.0.0.1:18080.
"""
import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

API_URL = os.environ.get("STRATA_API_URL", "http://127.0.0.1:18080/v1/chat/completions")
HEALTH_URL = os.environ.get("STRATA_HEALTH_URL", "http://127.0.0.1:18080/health")
API_KEY = os.environ.get("STRATA_API_KEY", "")
MODEL = "qwen3.8-flash-next-ud-iq3_xxs"
ENGINE_LOG = "/home/shinsuke/.local/state/strata/engine.log"


def get_gpu_stats():
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,memory.free,temperature.gpu,power.draw,clocks.gr,clocks.mem",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=5,
        ).strip()
        parts = [p.strip() for p in out.split(",")]
        return {
            "vram_used_mib": float(parts[0]),
            "vram_free_mib": float(parts[1]),
            "gpu_temp_c": float(parts[2]),
            "power_w": float(parts[3]),
            "clock_gr_mhz": float(parts[4]),
            "clock_mem_mhz": float(parts[5]),
        }
    except Exception as e:
        return {"error": str(e)}


def get_startup_info():
    info = {}
    try:
        with open(ENGINE_LOG, "r", errors="replace") as f:
            lines = f.readlines()
        # Scan backwards for latest startup lines
        for line in reversed(lines):
            if "expert_cache_slots" not in info:
                m = re.search(r"expert cache (\d+) slots, ([\d.]+) GiB", line)
                if m:
                    info["expert_cache_slots"] = int(m.group(1))
                    info["expert_cache_gib"] = float(m.group(2))
            if "draft_cap" not in info:
                m = re.search(r"DFlash2 drafter on .*?windows up to (\d+) tokens", line)
                if m:
                    info["dflash2_max_window"] = int(m.group(1))
            if "kv_streaming" not in info:
                m = re.search(r"KV streaming: (\d+) of (\d+) cells", line)
                if m:
                    info["kv_resident_cells"] = int(m.group(1))
                    info["kv_max_context"] = int(m.group(2))
            if "kv_grow" not in info and "kv-grow" in line:
                info["kv_grow_msg"] = line.strip()
            if "pool_workers" not in info:
                m = re.search(r"(\d+) pool workers on logical processors ([\d,]+)", line)
                if m:
                    info["pool_workers"] = int(m.group(1))
                    info["pool_cpus"] = m.group(2)
            if len(info) >= 5:
                break
    except Exception as e:
        info["error"] = str(e)
    return info


def parse_log_tail(start_line):
    parsed = {}
    try:
        with open(ENGINE_LOG, "r", errors="replace") as f:
            lines = f.readlines()[start_line:]
        for line in lines:
            m = re.search(
                r"decode expert cache hit rate: ([\d.]+)% \((\d+) hits / (\d+) lookups\); (\d+) more read by the GPU over PCIe.*?\(([\d.]+)% of all (\d+) routed\)",
                line,
            )
            if m:
                parsed["expert_hit_pct"] = float(m.group(1))
                parsed["expert_hits"] = int(m.group(2))
                parsed["expert_lookups"] = int(m.group(3))
                parsed["pcie_extra"] = int(m.group(4))
                parsed["pcie_routed_pct"] = float(m.group(5))
                parsed["total_routed"] = int(m.group(6))
            m2 = re.search(r"KV streaming: ([\d.]+)% of (\d+) block reads hit VRAM, ([\d.]+) MiB read from RAM", line)
            if m2:
                parsed["kv_vram_hit_pct"] = float(m2.group(1))
                parsed["kv_ram_read_mib"] = float(m2.group(3))
    except Exception:
        pass
    return parsed


def count_log_lines():
    try:
        with open(ENGINE_LOG, "r", errors="replace") as f:
            return sum(1 for _ in f)
    except Exception:
        return 0


def chat_request(messages, max_tokens=256, temperature=0.0, reasoning_effort="none", timeout=180):
    payload = {
        "model": MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "reasoning_effort": reasoning_effort,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
        },
        method="POST",
    )
    start_line = count_log_lines()
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    wall_s = time.perf_counter() - t0
    time.sleep(0.05)
    log_info = parse_log_tail(start_line)

    timings = body.get("timings", {})
    usage = body.get("usage", {})
    msg = body["choices"][0]["message"]
    content = msg.get("content") or ""
    reasoning = msg.get("reasoning_content") or ""

    draft_n = timings.get("draft_n", 0) or 0
    draft_acc = timings.get("draft_n_accepted", 0) or 0
    draft_acc_pct = (100.0 * draft_acc / draft_n) if draft_n > 0 else 0.0

    return {
        "wall_s": round(wall_s, 3),
        "prompt_n": timings.get("prompt_n", usage.get("prompt_tokens", 0)),
        "cache_n": timings.get("cache_n", 0),
        "predicted_n": timings.get("predicted_n", usage.get("completion_tokens", 0)),
        "prompt_ms": timings.get("prompt_ms", 0.0),
        "predicted_ms": timings.get("predicted_ms", 0.0),
        "prefill_tok_s": timings.get("prompt_per_second", 0.0),
        "decode_tok_s": timings.get("predicted_per_second", 0.0),
        "draft_n": draft_n,
        "draft_accepted": draft_acc,
        "draft_acc_pct": round(draft_acc_pct, 2),
        "finish_reason": body["choices"][0].get("finish_reason"),
        "content_preview": (content or reasoning)[:120],
        "content": content,
        "reasoning": reasoning,
        **log_info,
    }


def make_code_block(repeat_count):
    snippet = """
template <typename T, size_t Capacity>
class RingBuffer {
public:
    bool push(const T& item) {
        size_t head = head_.load(std::memory_order_relaxed);
        size_t next = (head + 1) % Capacity;
        if (next == tail_.load(std::memory_order_acquire)) return false;
        buf_[head] = item;
        head_.store(next, std::memory_order_release);
        return true;
    }
    bool pop(T& out) {
        size_t tail = tail_.load(std::memory_order_relaxed);
        if (tail == head_.load(std::memory_order_acquire)) return false;
        out = buf_[tail];
        tail_.store((tail + 1) % Capacity, std::memory_order_release);
        return true;
    }
private:
    std::array<T, Capacity> buf_;
    std::atomic<size_t> head_{0};
    std::atomic<size_t> tail_{0};
};
"""
    return "\n".join([f"// Block {i}\n{snippet}" for i in range(repeat_count)])


def build_cases(run_id, include_long=True):
    uid = f"{run_id}-{uuid.uuid4().hex[:8]}"
    cases = [
        {
            "name": "decode_code",
            "max_tokens": 512,
            "prompt": (
                f"[Run {uid}] Write a complete, production-quality C++20 thread-safe bounded MPMC queue "
                "using std::atomic and cache-line padding (alignas(64)) to avoid false sharing. "
                "Include enqueue, try_dequeue, and a brief explanation of memory ordering."
            ),
        },
        {
            "name": "decode_ja",
            "max_tokens": 512,
            "prompt": (
                f"[Run {uid}] Mixture-of-Experts (MoE) モデルの推論において、GPU VRAM 上のエキスパートキャッシュ、"
                "PCIe 経由のオンデマンド転送、および CPU メモリ上でのオフロード計算の 3 層構造がどのように協調して"
                "レイテンシとスループットを決定するか、技術的に詳しく日本語で解説してください。"
            ),
        },
        {
            "name": "prefill_mid",
            "max_tokens": 256,
            "prompt": (
                f"[Run {uid}] Review the following C++ code blocks and summarize the 3 main performance "
                "improvements (e.g., power-of-2 bitmask, cache-line alignment, move semantics):\n"
                + make_code_block(18)
            ),
        },
    ]
    if include_long:
        cases.append(
            {
                "name": "prefill_long",
                "max_tokens": 64,
                "prompt": (
                    f"[Run {uid}] Read all the following C++ blocks carefully. At the end, state ONLY "
                    "the exact token 'TARGET_FOUND_OK' and list the single bug in Block 42.\n"
                    + make_code_block(52)
                    + "\n// Block 42 special note: memory_order_relaxed used on release store!\n"
                    + make_code_block(4)
                ),
            }
        )
    return cases


def run_quality_gate():
    checks = []
    # 1. JSON check
    r1 = chat_request(
        [{"role": "user", "content": 'Return ONLY valid JSON with keys "status" ("ok") and "sum" (137*29). No markdown.'}],
        max_tokens=128,
        temperature=0.0,
    )
    txt1 = (r1["content"] or "").strip()
    # Strip markdown code fences if present
    txt1_clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt1, flags=re.DOTALL).strip()
    json_ok = False
    try:
        obj = json.loads(txt1_clean)
        json_ok = obj.get("status") == "ok" and int(obj.get("sum")) == 3973
    except Exception:
        json_ok = False
    checks.append({"name": "json_math", "pass": json_ok, "output": txt1[:160]})

    # 2. Japanese coherence & non-repetition
    r2 = chat_request(
        [{"role": "user", "content": "「東京」と「大阪」の人口規模の違いを日本語で2文で簡潔に述べてください。"}],
        max_tokens=160,
        temperature=0.0,
    )
    txt2 = (r2["content"] or "").strip()
    ja_ok = len(txt2) >= 20 and ("東京" in txt2 or "大阪" in txt2) and len(set(txt2)) > 10
    checks.append({"name": "ja_coherence", "pass": ja_ok, "output": txt2[:160]})

    return {
        "all_pass": all(c["pass"] for c in checks),
        "checks": checks,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--n", type=int, default=3)
    parser.add_argument("--out", default=None)
    parser.add_argument("--skip-long", action="store_true")
    parser.add_argument("--skip-quality", action="store_true")
    args = parser.parse_args()

    # Check health
    with urllib.request.urlopen(HEALTH_URL, timeout=10) as resp:
        h = json.loads(resp.read().decode("utf-8"))
        if not h.get("loaded"):
            print("ERROR: Model not loaded", file=sys.stderr)
            sys.exit(1)

    # Warmup 1 short call
    chat_request([{"role": "user", "content": "Reply with OK."}], max_tokens=16, temperature=0.0)

    startup = get_startup_info()
    gpu_before = get_gpu_stats()
    raw_runs = {}

    for i in range(args.n):
        cases = build_cases(f"{args.tag}-r{i}", include_long=not args.skip_long)
        for c in cases:
            res = chat_request(
                [{"role": "user", "content": c["prompt"]}],
                max_tokens=c["max_tokens"],
                temperature=0.0,
            )
            # Remove bulky text from saved raw
            res.pop("content", None)
            res.pop("reasoning", None)
            raw_runs.setdefault(c["name"], []).append(res)
            print(
                f"[{args.tag} iter={i+1}/{args.n}] {c['name']}: "
                f"prefill={res['prefill_tok_s']:.1f} tok/s (n={res['prompt_n']}, cache={res['cache_n']}), "
                f"decode={res['decode_tok_s']:.1f} tok/s (n={res['predicted_n']}), "
                f"hit={res.get('expert_hit_pct', 0):.1f}%, pcie={res.get('pcie_routed_pct', 0):.1f}%, "
                f"draft={res['draft_acc_pct']:.1f}%"
            )

    quality = None if args.skip_quality else run_quality_gate()
    gpu_after = get_gpu_stats()

    summary = {}
    for name, items in raw_runs.items():
        summary[name] = {
            "prefill_tok_s_median": round(statistics.median(x["prefill_tok_s"] for x in items), 2),
            "decode_tok_s_median": round(statistics.median(x["decode_tok_s"] for x in items), 2),
            "wall_s_median": round(statistics.median(x["wall_s"] for x in items), 3),
            "expert_hit_pct_median": round(statistics.median(x.get("expert_hit_pct", 0.0) for x in items), 2),
            "pcie_routed_pct_median": round(statistics.median(x.get("pcie_routed_pct", 0.0) for x in items), 2),
            "draft_acc_pct_median": round(statistics.median(x["draft_acc_pct"] for x in items), 2),
        }

    result = {
        "tag": args.tag,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "n": args.n,
        "startup": startup,
        "gpu_before": gpu_before,
        "gpu_after": gpu_after,
        "summary": summary,
        "quality": quality,
        "raw": raw_runs,
    }

    out_path = args.out or f"/home/shinsuke/StrataTurbo/results/{args.tag}.json"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print("\n=== SUMMARY:", args.tag, "===")
    print("Startup:", json.dumps(startup))
    for name, s in summary.items():
        print(
            f"  {name:12s}: decode={s['decode_tok_s_median']:6.2f} tok/s | "
            f"prefill={s['prefill_tok_s_median']:7.2f} tok/s | "
            f"hit={s['expert_hit_pct_median']:5.1f}% | pcie={s['pcie_routed_pct_median']:4.1f}% | "
            f"draft={s['draft_acc_pct_median']:5.1f}%"
        )
    if quality:
        print("Quality Gate:", "PASS" if quality["all_pass"] else f"FAIL {quality['checks']}")


if __name__ == "__main__":
    main()
