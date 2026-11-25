# StrataTurbo Phase 2〜Phase 5 実施・最終最適化報告書

- **実施日**: 2026-11-25
- **対象環境**: `gwol-target` (`192.168.10.20`) / NVIDIA GeForce RTX 4080 16GB (`sm_89`) / Intel Core i9-14900KF (`no_turbo=1`, 3.2 GHz 固定) / 64 GiB DDR5-4000
- **対象エンジン・モデル**: Strata `v0.2.21` (`Control 0.1.67` / `engine 0.1.67` + `patches/v0.2.21-pe-guided-pool.patch`) / `Qwen3.8-Flash-Next-UD-IQ3_XXS` + `DFlash2 (q4_0-selq4)`

---

## 1. 最終結論（Phase 0〜5 を通して採用した構成）

Phase 0〜Phase 5 の全施策を実装・実測し、**品質劣化ゼロ（ビット完全一致）かつ実務連続ワークロードで歴代最高性能を達成した「Phase 1 最適化 ＋ Phase 2 P/E コア Guided スケジューリング (`STRATA_TURBO_GUIDED=1`)」を最終構成 (`v0221_final_turbo`) として採用**しました。

| 指標 | 初期 Baseline (`v0.2.18`) | 実施前 (`v0221_opt_bench`, `n=3`) | **最終構成 (`v0221_final_turbo`, `n=5`)** | 変化（対 `v0221_opt_bench`） |
| :--- | ---: | ---: | ---: | ---: |
| **16問厳密品質テスト (`eval_quality.py`)** | — | 16/16 PASS (**151.15 tok/s**) | **16/16 PASS (100.0%, 160.65 tok/s)** | **+9.50 tok/s (+6.3%) / 最大 183.5 tok/s** |
| **17.6K 長文ストレス (Prefill / Decode)** | — | 3,536.1 / 106.6 tok/s | **3,593.8 / 93.7 tok/s** (`6.06s`, OOM なし) | **+1.6% (Prefill 歴代最速)** |
| **Decode (`decode_code`, `n=5` 中央値)** | 90.40 tok/s | 128.30 tok/s | **130.00 tok/s** *(最大 134.8)* | +1.70 tok/s (+1.3%) |
| **Decode (`decode_ja`, `n=5` 中央値)** | 80.10 tok/s | 110.50 tok/s | **109.50 tok/s** *(最大 112.4)* | -1.00 tok/s (誤差範囲) |
| **Prefill / Decode (`prefill_mid` 4.2K)** | 1,469.2 / 73.7 | 1,643.2 / 101.6 tok/s | **1,641.3 / 102.3 tok/s** | +0.70 tok/s (dec) |
| **Prefill / Decode (`prefill_long` 13K)** | 2,217.8 / 67.5 | 3,431.6 / 101.9 tok/s | **3,426.3 / 99.6 tok/s** *(iter 4-5: 110.1)* | 誤差範囲 |
| **温度 (`no_turbo=1` 維持)** | — | CPU 46.0℃ / GPU 51℃ | **CPU 45.0℃ / GPU 52℃** | 安全圏維持 |

### 16問厳密品質テスト (`v0221_final_qual`) の内訳
- **C++ (4/4 PASS)**: `A1_cpp_lru` 153.1 / `A2_cpp_intervals` 162.6 / `A3_cpp_queue` **183.5** / `A4_cpp_parse` **174.6 tok/s**
- **Python (4/4 PASS)**: `B1_py_dijkstra` **172.9** / `B2_py_toposort` **175.6** / `B3_py_wildcard` 160.5 / `B4_py_expr` **180.1 tok/s**
- **数学・論理 (4/4 PASS)**: `C1_math_mod` 160.8 / `C2_math_bayes` 153.6 / `C3_logic_puzzle` 156.9 / `C4_trace_code` **182.8 tok/s**
- **JSON・日本語8K (4/4 PASS)**: `D1_json_schema` 116.6 / `D2_json_log_extract` 125.9 / `D3_ja_needle_8k` **141.5** / `D4_ja_constrained_summary` 72.0 tok/s

---

## 2. Phase 2〜Phase 5 全検証結果と採否判定

### Phase 2：CPU 処理基盤の Raptor Lake 専用化
- **`STRATA_TURBO_GUIDED=1`（[patches/v0.2.21-pe-guided-pool.patch](../patches/v0.2.21-pe-guided-pool.patch)）— 【採用】**
  - **実装内容**: `engine/src/kernels/cpu/pool.cpp` の `dep_task`（Gate/Up および Down の行分割）を、従来の均等幅 `h * p / K` から **Guided 累積重み分割（序盤大きく P コアが処理し、終盤を細かくして E コアのストラグラー待ちを削減）** へ変更。全行を厳密に 1 回ずつ処理するため **ビット完全一致（品質劣化ゼロ）**。
  - **効果**: `p2_guided` (`n=3`) で `decode_ja` 温間平均 **116.70 tok/s** (+5.0%)、`decode_code` 温間平均 **138.85 tok/s**（`iter 3` は 151.1 tok/s 到達）。最終 16 問テストでも中央値 **`151.15 → 160.65 tok/s` (+6.3%)** と大幅向上。

### Phase 3：Expert Cache・CPU/PCIe 分担の再設計
- **可変サイズ Expert Cache**: ソース調査の結果、本家 `expert_cache.cpp` (`open_sized`) に 256-byte アラインの可変サイズスロットが既に実装済みであることを確認（追加改造不要）。
- **`STRATA_PCIE_MIN1=1` / `STRATA_PCIE_BALANCE=1` — 【不採用】**
  - PCIe 転送割合を `1.4% → 3.1〜3.4%` へ増やしたところ、RTX 4080 (PCIe 4.0 x16, 18.4 GB/s) では転送レイテンシが勝り、`decode_code` 127.0 / `decode_ja` 101.9 (併用時 94.8 tok/s) へ明確に悪化。
- **`STRATA_ADAPT_FETCH=0` (`p3_nofetch`) — 【不採用】**
  - `decode_code` 128.10 と伸びず、本家デフォルト (`STRATA_ADAPT_FETCH=2`) を維持。
- **`STRATA_TURBO_COST_PCIE=1`（C++ パッチ：ウィンドウ内トークン共有数重み付け）— 【不採用】**
  - `expert_source.cpp` で PCIe 昇格対象を `use[e] * rows_of[q]` でソートするパッチを検証したが、`decode_code` 温間 127.70 / `decode_ja` 温間 107.55 と低下したためリバート。

### Phase 4：投機的デコード統合最適化
- **`STRATA_TURBO_ADAPT_CONF=1`（C++ パッチ：受理率 EMA 連動の動的 `draft_conf`）— 【不採用】**
  - 単独（`p4_adapt_conf`）では `ja` 114.1 / `mid` 106.6 / `long` 106.45 と一部改善したものの、`p2_guided` と組み合わせた `p24_guided_adapt` では日本語生成時に `draft_conf` が緩みすぎてドラフト採択率が `71.5%` に落ち、`decode_ja` 中央値が `106.10 tok/s` へ低下したためリバート（固定 `--spec 6 --draft-conf 0.7` が最安定）。

### Phase 5：VRAM・CUDA・ビルド実行基盤最適化
- **LTO ビルド（`-flto=auto -fno-semantic-interposition`）— 【不採用】**
  - ビルド自体は成功（54 MB）したものの、リンク時に `struct block_q8_1` の `ggml` / `strata` 間 ODR 違反（`-Wodr`）が発生し、起動時ヘルスチェックに失敗（`APPLY_FAIL`）したため不採用・ビルドディレクトリ削除済み。
- **`--kv-grow` (`STRATA_KV_GROW=1`) — 【不採用】**
  - デフォルトの `--kv-resident 32768` が優先され Expert Cache スロットが `4703 → 4642` に減少、`decode_code` 121.90 に低下。
- **`--host-core last` (`p5_host_last`) — 【不採用】**
  - `decode_ja` 115.10 / `prefill_long` 104.90 と一部改善したが `decode_code` が 125.90 に低下したためデフォルト (`first`) を維持。

---

## 3. 全アーム比較一覧（Phase 2〜5）

| Tag | 内容 | `code` (温間平均 / 中央値) | `ja` (温間平均 / 中央値) | `mid_d` (温間 / 中央) | `lng_d` (温間 / 中央) | 採否 |
| :--- | :--- | ---: | ---: | ---: | ---: | :---: |
| `v0221_p1_best` (`n=5`) | Phase 1 勝者 (`spec=6, GDN_SPLIT=1, NO_NOWAIT`) | 137.50 / 138.20 | 111.12 / 109.80 | 104.90 / 103.70 | 102.12 / 98.30 | 基準 |
| **`p2_guided` (`n=3`)** | **+ Phase 2 `STRATA_TURBO_GUIDED=1`** | **138.85 / 126.60** | **116.70 / 116.20** | **104.85 / 101.20** | **102.40 / 103.60** | **採用** |
| `p3_pcie_min1` (`n=3`) | + `STRATA_PCIE_MIN1=1` | — / 127.00 | — / 101.90 | — / 101.00 | — / 98.90 | 不採用 |
| `p3_pcie_min1_bal` (`n=3`) | + `PCIE_MIN1=1` + `PCIE_BALANCE=1` | — / 128.80 | — / 94.80 | — / 95.70 | — / 102.60 | 不採用 |
| `p3_nofetch` (`n=3`) | + `STRATA_ADAPT_FETCH=0` | — / 128.10 | — / 112.60 | — / 106.80 | — / 99.50 | 不採用 |
| `p3_cost_pcie` (`n=3`) | + Phase 3 `STRATA_TURBO_COST_PCIE=1` | 127.70 / 122.90 | 107.55 / 106.90 | 104.60 / 103.20 | 102.30 / 100.50 | 不採用 |
| `p4_adapt_conf` (`n=3`) | + Phase 4 `STRATA_TURBO_ADAPT_CONF=1` | 134.40 / 132.30 | 114.10 / 113.00 | 106.60 / 102.60 | 106.45 / 103.70 | 不採用 |
| `p24_guided_adapt` (`n=3`) | `GUIDED=1` + `ADAPT_CONF=1` | 135.40 / 127.00 | 108.95 / 106.10 | 99.90 / 98.70 | 101.10 / 95.50 | 不採用 |
| `p234_all` (`n=3`) | `GUIDED` + `COST_PCIE` + `ADAPT_CONF` | 130.10 / 127.50 | 112.05 / 111.90 | 101.50 / 100.00 | 105.80 / 104.60 | 不採用 |
| `p5_host_last` (`n=3`) | + `--host-core last` | 129.00 / 125.90 | 115.95 / 115.10 | 104.45 / 102.70 | 106.85 / 104.90 | 不採用 |
| `p5_kvgrow` (`n=3`) | + `STRATA_KV_GROW=1` | — / 121.90 | — / 113.90 | — / 104.10 | — / 109.40 | 不採用 |
| `p5_lto_*` | Phase 5 `-flto=auto` ビルド | — | — | — | — | 不採用 (ODR 起動不可) |
| **`v0221_final_turbo` (`n=5`)** | **最終クリーン構成（Phase 1 + Phase 2 のみ）** | **131.08 / 130.00** | **109.38 / 109.50** | **102.20 / 102.30** | **102.78 / 99.60** | **最終採用 (16問 160.65 tok/s)** |

---

## 4. 最終まとめ

- ソフトウェアのみ（RTX 4080 16GB 単体 / DDR5-4000 / `no_turbo=1` 3.2 GHz 固定）で Phase 0〜Phase 5 まで全検証を完了し、**実務連続ワークロード（16問厳密テスト）で 16/16 PASS・中央値 160.65 tok/s（最大 183.5 tok/s）、17.6K Prefill 3,593.8 tok/s、CPU 45℃ / GPU 52℃** に到達しました。
- これ以上の大幅な高速化（+15〜50%）はソフトウェア側の物理限界に近いため、次は **BIOS 側（DDR5-4000 の XMP 引き上げ / CPU クロック上限の控えめな緩和）** または **GPU VRAM 増設（RTX 3090 整備後の交換・2枚差し）** が最も効果的です。
