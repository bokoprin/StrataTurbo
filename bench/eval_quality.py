#!/usr/bin/env python3
"""
StrataTurbo Strict 16-Question Quality A/B Evaluation Suite.
Includes C++ compile+run tests, Python unit tests, multi-step math/logic (reasoning=medium),
strict JSON schema checks, and 8K+ Japanese needle-in-a-haystack / constrained summary.
"""
import argparse
import hashlib
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid

API_URL = os.environ.get("STRATA_API_URL", "http://127.0.0.1:18080/v1/chat/completions")
HEALTH_URL = os.environ.get("STRATA_HEALTH_URL", "http://127.0.0.1:18080/health")
API_KEY = os.environ.get("STRATA_API_KEY", "")
MODEL = "qwen3.8-flash-next-ud-iq3_xxs"


def call_llm(prompt, max_tokens=1024, reasoning_effort="none", timeout=180):
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "reasoning_effort": reasoning_effort,
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
        },
        method="POST",
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    wall_s = time.perf_counter() - t0
    msg = body["choices"][0]["message"]
    content = (msg.get("content") or "").strip()
    timings = body.get("timings", {})
    return {
        "content": content,
        "decode_tok_s": timings.get("predicted_per_second", 0.0),
        "prefill_tok_s": timings.get("prompt_per_second", 0.0),
        "predicted_n": timings.get("predicted_n", 0),
        "wall_s": round(wall_s, 3),
    }


def extract_code(text, lang_hints=("cpp", "c++", "python", "py", "json")):
    # Try fenced code block first
    blocks = re.findall(r"```([a-zA-Z0-9_+-]*)\s*\n(.*?)```", text, flags=re.DOTALL)
    if blocks:
        for lang, code in blocks:
            if lang.lower() in lang_hints:
                return code.strip()
        return blocks[0][1].strip()
    return text.strip()


def run_cpp_test(code, test_main):
    full_src = (
        "#include <bits/stdc++.h>\n"
        "using namespace std;\n"
        f"{code}\n"
        f"{test_main}\n"
    )
    with tempfile.TemporaryDirectory() as td:
        src_p = os.path.join(td, "test.cpp")
        bin_p = os.path.join(td, "test_bin")
        with open(src_p, "w", encoding="utf-8") as f:
            f.write(full_src)
        comp = subprocess.run(
            ["g++", "-std=c++20", "-O2", "-pthread", src_p, "-o", bin_p],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if comp.returncode != 0:
            return False, f"compile_error: {comp.stderr[:300]}"
        run = subprocess.run([bin_p], capture_output=True, text=True, timeout=10)
        if run.returncode != 0:
            return False, f"runtime_error(rc={run.returncode}): {run.stderr[:200]} {run.stdout[:200]}"
        return True, "ok"


def run_py_test(code, test_code):
    full_src = f"{code}\n\n{test_code}\n"
    with tempfile.TemporaryDirectory() as td:
        src_p = os.path.join(td, "test.py")
        with open(src_p, "w", encoding="utf-8") as f:
            f.write(full_src)
        run = subprocess.run(
            [sys.executable, src_p],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if run.returncode != 0:
            return False, f"py_error(rc={run.returncode}): {run.stderr[:300]}"
        return True, "ok"


def build_questions(run_uid):
    questions = []

    # --- A. C++ (4 questions) ---
    questions.append({
        "id": "A1_cpp_lru",
        "category": "cpp",
        "reasoning": "none",
        "max_tokens": 768,
        "prompt": (
            f"[Q {run_uid}] Implement a C++20 class `LRUCache` with constructor `LRUCache(int capacity)`, "
            "`int get(int key)` (returns -1 if missing), and `void put(int key, int value)` in O(1) average time. "
            "Do NOT define `main()`. Output ONLY a ```cpp code block."
        ),
        "verify": lambda txt: run_cpp_test(
            extract_code(txt),
            """
int main() {
    LRUCache c(2);
    c.put(1, 10); c.put(2, 20);
    assert(c.get(1) == 10);
    c.put(3, 30); // evicts 2
    assert(c.get(2) == -1);
    c.put(4, 40); // evicts 1
    assert(c.get(1) == -1);
    assert(c.get(3) == 30);
    assert(c.get(4) == 40);
    c.put(4, 44);
    assert(c.get(4) == 44);
    return 0;
}
""",
        ),
    })

    questions.append({
        "id": "A2_cpp_intervals",
        "category": "cpp",
        "reasoning": "none",
        "max_tokens": 512,
        "prompt": (
            f"[Q {run_uid}] Implement a C++20 function `vector<pair<int,int>> merge_intervals(vector<pair<int,int>> intervals)` "
            "that merges all overlapping or touching intervals (e.g. [1,3] and [3,5] -> [1,5]) and returns them sorted ascending. "
            "Handle empty input. Do NOT define `main()`. Output ONLY a ```cpp code block."
        ),
        "verify": lambda txt: run_cpp_test(
            extract_code(txt),
            """
int main() {
    assert(merge_intervals({}).empty());
    auto r1 = merge_intervals({{1,3},{2,6},{8,10},{15,18}});
    assert((r1 == vector<pair<int,int>>{{1,6},{8,10},{15,18}}));
    auto r2 = merge_intervals({{5,7},{1,3},{3,5}});
    assert((r2 == vector<pair<int,int>>{{1,7}}));
    auto r3 = merge_intervals({{1,10},{2,3},{4,8}});
    assert((r3 == vector<pair<int,int>>{{1,10}}));
    return 0;
}
""",
        ),
    })

    questions.append({
        "id": "A3_cpp_queue",
        "category": "cpp",
        "reasoning": "none",
        "max_tokens": 768,
        "prompt": (
            f"[Q {run_uid}] Implement a C++20 thread-safe class `BoundedQueue` for `int` with constructor `BoundedQueue(size_t cap)`, "
            "`bool try_push(int v)` (returns false if full), and `bool try_pop(int& out)` (returns false if empty). "
            "Do NOT define `main()`. Output ONLY a ```cpp code block."
        ),
        "verify": lambda txt: run_cpp_test(
            extract_code(txt),
            """
int main() {
    BoundedQueue q(3);
    int out = -1;
    assert(!q.try_pop(out));
    assert(q.try_push(1)); assert(q.try_push(2)); assert(q.try_push(3));
    assert(!q.try_push(4));
    assert(q.try_pop(out) && out == 1);
    assert(q.try_push(4));
    assert(q.try_pop(out) && out == 2);
    assert(q.try_pop(out) && out == 3);
    assert(q.try_pop(out) && out == 4);
    assert(!q.try_pop(out));
    return 0;
}
""",
        ),
    })

    questions.append({
        "id": "A4_cpp_parse",
        "category": "cpp",
        "reasoning": "none",
        "max_tokens": 512,
        "prompt": (
            f"[Q {run_uid}] Implement a C++20 function `vector<int> parse_csv_ints(const string& s)` that parses comma-separated integers. "
            "Skip empty segments (e.g. \"1,, -2, 3,\" -> {1, -2, 3}) and trim whitespace around numbers. "
            "Do NOT define `main()`. Output ONLY a ```cpp code block."
        ),
        "verify": lambda txt: run_cpp_test(
            extract_code(txt),
            """
int main() {
    assert((parse_csv_ints("") == vector<int>{}));
    assert((parse_csv_ints("1,, -2, 3,") == vector<int>{1, -2, 3}));
    assert((parse_csv_ints("  42 ") == vector<int>{42}));
    assert((parse_csv_ints("0,-100,999") == vector<int>{0, -100, 999}));
    return 0;
}
""",
        ),
    })

    # --- B. Python Algorithms (4 questions) ---
    questions.append({
        "id": "B1_py_dijkstra",
        "category": "python",
        "reasoning": "none",
        "max_tokens": 512,
        "prompt": (
            f"[Q {run_uid}] Implement a Python function `shortest_path(n, edges, src, dst)` where nodes are `0..n-1`, "
            "`edges` is a list of `(u, v, w)` directed edges with `w >= 0`. Return the minimum total weight from `src` to `dst`, "
            "or `-1` if unreachable. Output ONLY a ```python code block."
        ),
        "verify": lambda txt: run_py_test(
            extract_code(txt),
            """
assert shortest_path(4, [(0,1,4),(0,2,1),(2,1,2),(1,3,1),(2,3,5)], 0, 3) == 4
assert shortest_path(3, [(0,1,5)], 0, 2) == -1
assert shortest_path(1, [], 0, 0) == 0
assert shortest_path(4, [(0,1,0),(1,2,0),(2,3,7)], 0, 3) == 7
""",
        ),
    })

    questions.append({
        "id": "B2_py_toposort",
        "category": "python",
        "reasoning": "none",
        "max_tokens": 512,
        "prompt": (
            f"[Q {run_uid}] Implement a Python function `topo_order(n, edges)` for nodes `0..n-1` and directed edges `(u, v)` meaning `u` before `v`. "
            "Return a valid topological ordering as a list of length `n`, or `None` if a cycle exists. "
            "Output ONLY a ```python code block."
        ),
        "verify": lambda txt: run_py_test(
            extract_code(txt),
            """
def valid(n, edges, order):
    if order is None or len(order) != n or set(order) != set(range(n)):
        return False
    pos = {v: i for i, v in enumerate(order)}
    return all(pos[u] < pos[v] for u, v in edges)

assert valid(4, [(0,1),(0,2),(1,3),(2,3)], topo_order(4, [(0,1),(0,2),(1,3),(2,3)]))
assert topo_order(3, [(0,1),(1,2),(2,0)]) is None
assert topo_order(2, [(0,0)]) is None
assert valid(3, [], topo_order(3, []))
""",
        ),
    })

    questions.append({
        "id": "B3_py_wildcard",
        "category": "python",
        "reasoning": "none",
        "max_tokens": 512,
        "prompt": (
            f"[Q {run_uid}] Implement a Python function `is_match(s: str, p: str) -> bool` supporting `?` (matches any single char) "
            "and `*` (matches zero or more chars) over the entire string `s`. Use DP or memoization so large `*` patterns do not time out. "
            "Output ONLY a ```python code block."
        ),
        "verify": lambda txt: run_py_test(
            extract_code(txt),
            """
assert is_match("aa", "a") is False
assert is_match("aa", "*") is True
assert is_match("cb", "?a") is False
assert is_match("adceb", "*a*b") is True
assert is_match("acdcb", "a*c?b") is False
assert is_match("", "***") is True
assert is_match("a" * 30 + "b", "*a*a*a*a*c") is False
""",
        ),
    })

    questions.append({
        "id": "B4_py_expr",
        "category": "python",
        "reasoning": "none",
        "max_tokens": 640,
        "prompt": (
            f"[Q {run_uid}] Implement a Python function `eval_expr(expr: str) -> int` that evaluates an integer arithmetic expression "
            "containing non-negative integers, `+`, `-`, `*`, `/`, spaces, and parentheses `(` `)`. "
            "Integer division `/` truncates toward zero (e.g. `-7 / 2 == -3`). Output ONLY a ```python code block."
        ),
        "verify": lambda txt: run_py_test(
            extract_code(txt),
            """
assert eval_expr("3 + 2 * 2") == 7
assert eval_expr(" (1+(4+5+2)-3)+(6+8) ") == 23
assert eval_expr("14 - 3 / 2") == 13
assert eval_expr("(0 - 7) / 2") == -3
assert eval_expr("2 * (3 + 4 * (5 - 1))") == 38
""",
        ),
    })

    # --- C. Math, Logic, Code Trace (4 questions, reasoning=medium) ---
    questions.append({
        "id": "C1_math_mod",
        "category": "reasoning",
        "reasoning": "medium",
        "max_tokens": 1024,
        "prompt": (
            f"[Q {run_uid}] Compute the exact last two digits (i.e. value mod 100) of 7^2026. "
            "End your response with `FINAL: <number>`."
        ),
        "verify": lambda txt: (
            bool(re.search(r"FINAL:\s*0?49\b", txt)),
            f"expected FINAL: 49, got tail={txt[-120:]!r}",
        ),
    })

    questions.append({
        "id": "C2_math_bayes",
        "category": "reasoning",
        "reasoning": "medium",
        "max_tokens": 1024,
        "prompt": (
            f"[Q {run_uid}] A disease affects 1% of a population. A test has 95% sensitivity (true positive rate) "
            "and 5% false positive rate. If a randomly chosen person tests positive, what is the probability (in percent, "
            "rounded to 2 decimal places, e.g., 12.34%) that they actually have the disease? "
            "End your response with `FINAL: <value>%`."
        ),
        "verify": lambda txt: (
            bool(re.search(r"FINAL:\s*16\.10?%", txt)),
            f"expected FINAL: 16.10%, got tail={txt[-120:]!r}",
        ),
    })

    questions.append({
        "id": "C3_logic_puzzle",
        "category": "reasoning",
        "reasoning": "medium",
        "max_tokens": 1024,
        "prompt": (
            f"[Q {run_uid}] Five servers S1, S2, S3, S4, S5 are arranged in racks 1 to 5 (left to right):\n"
            "1. S3 is immediately to the right of S1.\n"
            "2. S5 is in rack 5.\n"
            "3. S2 is to the left of S4, and there is exactly one rack between S2 and S4.\n"
            "4. S1 is not in rack 1.\n"
            "Determine the exact order from rack 1 to 5. End your response with `FINAL: Sx,Sy,Sz,Sw,Sv` (no spaces)."
        ),
        # Check: S5=5. S2 _ S4 -> (1,3) or (2,4). If (1,3), remaining 2,4 for S1,S3 -> impossible for S3=S1+1.
        # Wait: if S2=2, S4=4, remaining 1,3 -> impossible for S3=S1+1!
        # Let's fix puzzle so it has a unique valid solution:
        # S2=1, S4=3 -> remaining 2,4 for S1,S3 -> impossible.
        # What if S5=1, S2 _ S4 at (2,4), remaining 3,5? Impossible.
        # What if S1,S3 adjacent at (3,4), S5=5, S2=1, S4=2? Then condition 3: "S2 is immediately left of S4" -> (1,2),(3,4),5!
        "verify": lambda txt: (True, "placeholder"),
    })
    # Replace C3 with guaranteed unique solution puzzle:
    questions[-1] = {
        "id": "C3_logic_puzzle",
        "category": "reasoning",
        "reasoning": "medium",
        "max_tokens": 1024,
        "prompt": (
            f"[Q {run_uid}] Five servers S1, S2, S3, S4, S5 are placed in racks 1 to 5 (left to right):\n"
            "1. S5 is in rack 5.\n"
            "2. S3 is immediately to the right of S1.\n"
            "3. S2 is to the left of S1, and S4 is in rack 1.\n"
            "Determine the unique order from rack 1 to 5. End your response with `FINAL: Sx,Sy,Sz,Sw,Sv` (no spaces)."
        ),
        # Unique solution: S4=1, S2=2, S1=3, S3=4, S5=5 -> S4,S2,S1,S3,S5
        "verify": lambda txt: (
            bool(re.search(r"FINAL:\s*S4,S2,S1,S3,S5\b", txt)),
            f"expected FINAL: S4,S2,S1,S3,S5, got tail={txt[-120:]!r}",
        ),
    }

    questions.append({
        "id": "C4_trace_code",
        "category": "reasoning",
        "reasoning": "medium",
        "max_tokens": 1024,
        "prompt": (
            f"[Q {run_uid}] What exact integer does `f(6, 3)` return in the following C++ code?\n"
            "```cpp\n"
            "int f(int n, int k) {\n"
            "    if (n <= 1) return n + k;\n"
            "    if (n % 2 == 0) return f(n / 2, k + 1) + (n ^ k);\n"
            "    return f(n - 1, k * 2) - 1;\n"
            "}\n"
            "```\n"
            "End your response with `FINAL: <number>`."
        ),
        # Trace:
        # f(6,3): even -> f(3, 4) + (6^3 = 5)
        # f(3,4): odd  -> f(2, 8) - 1
        # f(2,8): even -> f(1, 9) + (2^8 = 10)
        # f(1,9): 1+9 = 10
        # f(2,8) = 10 + 10 = 20
        # f(3,4) = 20 - 1 = 19
        # f(6,3) = 19 + 5 = 24
        "verify": lambda txt: (
            bool(re.search(r"FINAL:\s*24\b", txt)),
            f"expected FINAL: 24, got tail={txt[-120:]!r}",
        ),
    })

    # --- D. Strict JSON Schema & 8K+ Japanese Needle / Summary (4 questions) ---
    def check_json1(txt):
        try:
            obj = json.loads(extract_code(txt, ("json",)))
            assert obj["service"] == "strata-turbo"
            assert isinstance(obj["port"], int) and obj["port"] == 18080
            assert obj["gpu"]["arch"] == "sm_89"
            assert obj["gpu"]["vram_gb"] == 16
            assert isinstance(obj["flags"], list) and len(obj["flags"]) == 3
            assert all(isinstance(x, str) for x in obj["flags"])
            assert obj["mode"] in ("production", "staging")
            return True, "ok"
        except Exception as e:
            return False, f"json1_err: {e}; raw={txt[:150]!r}"

    questions.append({
        "id": "D1_json_schema",
        "category": "json_ja",
        "reasoning": "none",
        "max_tokens": 384,
        "prompt": (
            f"[Q {run_uid}] Output ONLY valid JSON (no markdown, no explanation) matching this exact schema:\n"
            '- "service": "strata-turbo"\n'
            '- "port": 18080 (integer)\n'
            '- "gpu": {"arch": "sm_89", "vram_gb": 16}\n'
            '- "flags": array of exactly 3 strings\n'
            '- "mode": either "production" or "staging"\n'
        ),
        "verify": check_json1,
    })

    def check_json2(txt):
        try:
            obj = json.loads(extract_code(txt, ("json",)))
            assert obj["total_errors"] == 3
            assert obj["critical_hosts"] == ["node-b", "node-d"] or set(obj["critical_hosts"]) == {"node-b", "node-d"}
            assert obj["max_latency_ms"] == 420
            return True, "ok"
        except Exception as e:
            return False, f"json2_err: {e}; raw={txt[:150]!r}"

    questions.append({
        "id": "D2_json_log_extract",
        "category": "json_ja",
        "reasoning": "none",
        "max_tokens": 384,
        "prompt": (
            f"[Q {run_uid}] Parse the logs below and output ONLY valid JSON with keys "
            '`total_errors` (int: count of ERROR/CRITICAL lines), `critical_hosts` (list of hostnames with CRITICAL), '
            'and `max_latency_ms` (int: highest latency_ms across all lines):\n'
            "2026-11-22 10:00:01 INFO host=node-a latency_ms=120\n"
            "2026-11-22 10:00:02 ERROR host=node-c latency_ms=310\n"
            "2026-11-22 10:00:03 CRITICAL host=node-b latency_ms=420\n"
            "2026-11-22 10:00:04 INFO host=node-a latency_ms=95\n"
            "2026-11-22 10:00:05 CRITICAL host=node-d latency_ms=280\n"
        ),
        "verify": check_json2,
    })

    filler_para = (
        "分散推論基盤の運用においては、GPUのVRAM容量とPCIeバス帯域、ホスト側DDR5メモリの並列アクセス効率を総合的に監視する必要がある。"
        "特にMixture-of-Expertsアーキテクチャでは、トークンごとに動的に選択されるエキスパートの局所性を利用してホットな重みをVRAMへ常駐させ、"
        "コールドなエキスパートをホストメモリからオンデマンド転送またはCPUスレッドプールで並列計算することで、限られたVRAM容量でも大規模モデルを実用速度で動作させることができる。"
    )
    haystack = "\n".join([f"[記録 {i}] {filler_para}" for i in range(45)])
    needle = (
        "\n[重要設定メモ] 監視対象ノード『サーバーB』の専用待受ポート番号は 29417、"
        "タイムアウト閾値は 4500ms、主担当エンジニア名は 高橋健太 である。\n"
    )
    haystack2 = "\n".join([f"[記録 {i+45}] {filler_para}" for i in range(45)])

    questions.append({
        "id": "D3_ja_needle_8k",
        "category": "json_ja",
        "reasoning": "none",
        "max_tokens": 256,
        "prompt": (
            f"[Q {run_uid}] 以下の長文記録から『サーバーB』に関する「専用待受ポート番号」「タイムアウト閾値」「主担当エンジニア名」を正確に抽出し、"
            "JSON形式 (`{\"port\": ..., \"timeout_ms\": ..., \"owner\": \"...\"}`) のみで回答してください。\n\n"
            f"{haystack}\n{needle}\n{haystack2}"
        ),
        "verify": lambda txt: (
            ("29417" in txt and "4500" in txt and "高橋健太" in txt),
            f"missing needle values in {txt[:200]!r}",
        ),
    })

    def check_ja_summary(txt):
        lines = [ln.strip() for ln in txt.strip().splitlines() if ln.strip()]
        if len(lines) != 3:
            return False, f"expected 3 lines, got {len(lines)}: {lines}"
        for idx, ln in enumerate(lines):
            if len(ln) > 55:  # allow bullet prefix (~5 chars) + 50 chars
                return False, f"line {idx+1} too long ({len(ln)} chars): {ln}"
        joined = "\n".join(lines)
        for kw in ("VRAM", "PCIe", "CPU"):
            if kw not in joined:
                return False, f"missing keyword {kw}"
        return True, "ok"

    questions.append({
        "id": "D4_ja_constrained_summary",
        "category": "json_ja",
        "reasoning": "none",
        "max_tokens": 256,
        "prompt": (
            f"[Q {run_uid}] 以下の制約をすべて厳守して、MoE推論の高速化手法を日本語で要約してください。\n"
            "制約1: 箇条書きでちょうど3行のみ出力すること（前置きや結びの文は禁止）。\n"
            "制約2: 各行は50文字以内に収めること。\n"
            "制約3: 「VRAM」「PCIe」「CPU」の3つの単語をすべて含めること。\n"
        ),
        "verify": check_ja_summary,
    })

    return questions


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    with urllib.request.urlopen(HEALTH_URL, timeout=10) as resp:
        h = json.loads(resp.read().decode("utf-8"))
        if not h.get("loaded"):
            print("ERROR: Model not loaded", file=sys.stderr)
            sys.exit(1)

    uid = uuid.uuid4().hex[:8]
    questions = build_questions(uid)
    results = []
    passed = 0

    for q in questions:
        resp = call_llm(
            q["prompt"],
            max_tokens=q["max_tokens"],
            reasoning_effort=q["reasoning"],
        )
        ok, detail = q["verify"](resp["content"])
        if ok:
            passed += 1
        h_out = hashlib.sha256(resp["content"].encode("utf-8")).hexdigest()[:12]
        results.append({
            "id": q["id"],
            "category": q["category"],
            "pass": ok,
            "detail": detail,
            "decode_tok_s": resp["decode_tok_s"],
            "prefill_tok_s": resp["prefill_tok_s"],
            "predicted_n": resp["predicted_n"],
            "output_hash": h_out,
            "output": resp["content"],
        })
        status = "PASS" if ok else f"FAIL ({detail})"
        print(f"[{args.tag}] {q['id']:26s} -> {status} | dec={resp['decode_tok_s']:.1f} tok/s")

    out_path = args.out or f"/home/shinsuke/StrataTurbo/results/{args.tag}.json"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    summary = {
        "tag": args.tag,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "passed": passed,
        "total": len(questions),
        "pass_rate_pct": round(100.0 * passed / len(questions), 1),
        "median_decode_tok_s": round(statistics.median(r["decode_tok_s"] for r in results), 2),
        "results": results,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print(f"\n=== QUALITY SUMMARY ({args.tag}): {passed}/{len(questions)} PASS ({summary['pass_rate_pct']}%), median_dec={summary['median_decode_tok_s']} tok/s ===")


if __name__ == "__main__":
    main()
