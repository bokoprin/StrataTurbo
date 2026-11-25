# StrataTurbo 最適化まとめ＆今後の高速化候補（相談用資料）

> **目的**: NVIDIA GeForce RTX 4080 (16GB, `sm_89`) + Intel Core i9-14900KF 環境で、[Niko1221/Strata](https://github.com/Niko1221/Strata) 上の **Qwen3.8-Flash-Next-UD-IQ3_XXS** を品質劣化なく最大限に高速化するための、これまでの実施済み施策と残っている候補のまとめです。

---

## 1. 環境・前提・制約条件

- **推論エンジン**: `Niko1221/Strata` `v0.2.21`（Control `0.1.67` / engine `0.1.67`、ネイティブビルド）
- **モデル**: `Qwen3.8-Flash-Next-UD-IQ3_XXS`（GGUF 合計 約 79 GB + pack 1.4 GB）
- **投機的デコード (Draft)**: `DFlash2` (`qwen3.8-flash-next-dflash2.q4_0-selq4.pack`, `dflash2_max_window=6`)
- **GPU**: NVIDIA GeForce RTX 4080 16GB (`sm_89` / VRAM 帯域 717 GB/s / L2 64 MB)
- **CPU**: Intel Core i9-14900KF（8 P-cores + 16 E-cores / 32 threads）
  - **制約**: 発熱抑制のため **`no_turbo=1`（Pコア 3.2 GHz / Eコア 2.4 GHz 固定）** で運用中
- **RAM**: 64 GiB（実効 62 GiB, DDR5-4000 / 稼働時使用量 ~57 GiB, 空き ~5.1 GiB）
- **マザーボード**: `MouseComputer Z790-S01`
- **品質・安定性要件**:
  - 16問厳密品質テスト（C++ 4問 / Python 4問 / 数学・論理 4問 / JSON・日本語8K針探し 4問）で **15/16〜16/16 PASS** を維持
  - 17.6K 長文ストレステストで OOM しないこと

---

## 2. 現在の最終構成と実測性能（`v0.2.21`）

### ビルド設定（`scripts/build_rtx4080.sh`）
- CUDA 12.8 / `CMAKE_CUDA_ARCHITECTURES="89-real"`
- `-O3 -march=raptorlake -mtune=raptorlake`（AVX-VNNI 有効）
- `-DSTRATA_NATIVE_EXPERTS=ON -DSTRATA_PORTABLE=OFF -DSTRATA_Q6K_EXPERTS=ON`

### 起動フラグ・環境変数
- **CLI フラグ**:
  - `--kv k8v4 --kv-resident 20480 --vram-reserve-mib 96`
  - `--pool-workers 23 --aux-cpus auto --adapt-min-gain 2.0`
  - `--spec 4 --draft-conf 0.7 --draft dflash2 --vram-elastic`
- **環境変数**:
  - `STRATA_NV_CHUNK=1`
  - `STRATA_POOL_SPIN_US=2000`
  - `STRATA_ADAPT_LAG=2`
  - `STRATA_ADAPT_NOWAIT=1`
  - `STRATA_CPU_IQ_ALL_EXACT1=1`
  - `STRATA_ROUTE_TAIL_SKIP=7`
  - `STRATA_CACHE_PIN_FRAC=0`
- *(注: `v0.2.19` 以降は `nv_defaults.cpp` に `sm_89` 向けデフォルト一式が本家統合済み：`STRATA_HC_UPMIX=1`, `STRATA_Q80_ROWS1=1`, `STRATA_EXP_MODE=8`, `STRATA_SIDE_MIXER=2`, `STRATA_DEQUANT_SIDE=1`, `STRATA_LFUSE=1`, `STRATA_PLE_COMPACT=1`, `STRATA_PLE_COPY_STREAM=1`, `STRATA_PF_FUSED_KQ=1` 等)*

### ベンチマーク推移（Baseline → 現在）

| 指標 | Baseline (`v0.2.18` 初期) | `v0.2.18` (`final_max_opt`) | `v0.2.20` (`v0220_opt_best`) | **現在 `v0.2.21` (`v0221_opt_bench`)** |
| :--- | ---: | ---: | ---: | ---: |
| **Decode (`decode_code` 短文)** | 90.40 tok/s | 136.70 tok/s *(最大 140.5)* | 129.70 tok/s *(最大 145.7)* | **128.30 tok/s** *(hit 80.5%, draft 87.2%)* |
| **Decode (`decode_ja` 短文)** | 80.10 tok/s | 113.10 tok/s *(最大 115.3)* | 109.40 tok/s *(最大 114.4)* | **110.50 tok/s** *(hit 81.8%, draft 79.5%)* |
| **Prefill / Decode (`prefill_mid` 4.2K)** | 1,469.2 / 73.7 | 1,545.1 / 103.1 | 1,644.3 / 101.9 | **1,643.2 / 101.6 tok/s** |
| **Prefill / Decode (`prefill_long` 13K)** | 2,217.8 / 67.5 | 3,438.3 / 103.6 | 3,438.4 / 109.2 | **3,431.6 / 101.9 tok/s** |
| **17.6K 長文ストレス** | — | 3,552.5 / — | 3,583.5 / 99.3 | **3,536.1 / 106.6 tok/s** (`5.97s`, OOM なし) |
| **Expert Cache (VRAM)** | 3,701 slots (7.08 GiB) | 4,770 slots (9.09 GiB) | 4,704 slots (8.97 GiB) | **4,704 slots (8.97 GiB)** *(空き VRAM 89 MiB)* |
| **16問厳密品質テスト** | — | 15/16〜16/16 (148.1 tok/s) | 15/16 (146.6 tok/s) | **16/16 PASS (100%, median 151.15 tok/s)** |
| **温度 (`no_turbo=1`)** | — | CPU 48℃ / GPU 54℃ | CPU 50℃ / GPU 55℃ | **CPU 46℃ / GPU 51℃** |

### 現在のボトルネック構造
- VRAM の Expert Cache ヒット率は **約 75〜84%**。
- 残りの **16〜25% のキャッシュミス分を CPU（3.2 GHz 固定 / DDR5-4000）がシステム RAM から読んで計算**しており、ここが Decode 速度の最大ボトルネックになっている。

---

## 3. これまでに実施した最適化（採用済み）

1. **VRAM 再配分による Expert Cache 最大化**
   - `--kv k8v4 --kv-resident 20480 --vram-reserve-mib 96` により、Expert Cache を `3,701` → `4,704` スロット（8.97 GiB）へ拡張。
2. **キャッシュピン留め解除 (`STRATA_CACHE_PIN_FRAC=0`)**
   - `v0.2.20` 以降のデフォルトピン留め（上位 25% 固定）を解除し、動的キャッシュ効率を改善（`decode_code` +3.7%）。
3. **RTX 4080 (`sm_89`) + Raptor Lake 専用ネイティブビルド**
   - `89-real` + `-march=raptorlake`（AVX-VNNI 有効）。
4. **ルーティング末尾スキップ (`STRATA_ROUTE_TAIL_SKIP=7`)**
   - 16問厳密品質テストで厳格版 (`0`) と同等以上の正答率（16/16 PASS）を確認した上で正式採用。Decode を大幅に底上げ（118 → 147+ tok/s）。
5. **CPU プール・長文チャンク・非同期適応キャッシュ最適化**
   - `--pool-workers 23 --aux-cpus auto --adapt-min-gain 2.0`、`STRATA_NV_CHUNK=1`、`STRATA_POOL_SPIN_US=2000`、`STRATA_ADAPT_LAG=2`、`STRATA_ADAPT_NOWAIT=1`、`STRATA_CPU_IQ_ALL_EXACT1=1`。
6. **投機的デコード (`DFlash2`) の信頼度ベース動的打ち切り**
   - `--spec 4 --draft-conf 0.7` を採用（確信度 0.7 未満で動的打ち切り）。

---

## 4. すでに検証済みで「不採用／効果薄」だった施策

*(※他の AI に相談する際、以下の検証済み項目は重複提案を避けるための記録です)*

- **投機的デコードの大幅パラメータ変更 (`d1`〜`d8`, `dc_learn`, `df1`〜`df8`)**:
  - `--spec 6 / 8` × `--draft-conf 0.4 / 0.5 / 0.6 / 0.8 / 0.85`、`cap 8/10/12`、`chain4`、`draft_costs` 学習、`df_branch`、`lookup_chain2`、`bf16_sel`、`head_native` を網羅的に A/B 比較済み。大きく伸ばせる設定はなかった（※`--spec 6 --draft-conf 0.7` のみ小幅増の余地あり、後述）。
- **CPU ワーカー数変更 (`--pool-workers 15 / 19 / 20 / 24`)**:
  - `opt5_w15` / `opt6_w19` / `s6` / `p3_w24` すべて検証済み。現状は **`23` スレッド**（物理 Pコア 7 + Eコア 16 相当、1コアを GPU 制御用 `aux` に温存）が最速。
- **未採用カーネルノブ (`k1`〜`k6`, `opt3`〜`opt4`)**:
  - `STRATA_GR_V3=1`, `STRATA_FUSED_SWIGLU_Q81=1`, `STRATA_ATTN_V3=1`, `STRATA_L2_PF_BLOCKS=16/24`, `STRATA_POOL_UMWAIT=1`, `STRATA_HC_Q8_TC=2` は総合速度で低下、`STRATA_HC_Q8_TC=0` は HTTP 503 で不採用。
- **PCIe 分担比率の手動固定 (`--pcie-frac 0.25 / 0.35 / 0.50`)**:
  - 自動プローブを上回らず不採用。
- **PGO (Profile-Guided Optimization) ビルド (`pgo_ab`, `pgo_real`)**:
  - CLI 計測プロファイルではサーバーループのホットパスが合わず、`decode_code` が 125.1 → 113.2 tok/s に低下したため不採用。
- **カスタム `expert-profile.bin` 再学習 (`prof_custom_ab`)**:
  - 混合ワークロードから再学習したが、元プロファイルの方が速く（`decode_ja` 113.7 vs 107.5 tok/s）不採用。
- **`STRATA_ROUTE_TAIL_SKIP=8` (`t6_tail_skip8`)**:
  - `7` で頭打ち・品質リスク増のため `7` を上限とした。
- **PLE 無効化 (`v0219_clean_nople`)**:
  - `decode_code` が低下したため有効を維持。

---

## 5. 残っている最適化候補

### A. ソフトウェア設定の残り（手軽・品質維持 / 見込み +1〜3%）
1. **`--spec 6 --draft-conf 0.7` の `v0.2.21` での再検証**
   - 過去の `df1` (`spec 4`) vs `df3` (`spec 6`) で `decode_code` `131.3 → 135.3 tok/s (+4.0)`、`decode_ja` `109.5 → 111.6 tok/s (+2.1)` の伸びがあったため、`v0.2.21` 上で再検証する余地あり。
2. **`STRATA_PREFILL_CPU_SHARE_MAX=7424` 等の Prefill 微調整**
   - 中長文チャンクの CPU シェア閾値の調整。
3. **`v0.2.18` (`136.7 tok/s`) と `v0.2.21` (`128.3 tok/s`) の初速差の原因切り分け**
   - `v0.2.19` 以降でデフォルト化されたフラグ（`STRATA_LFUSE`, `STRATA_PLE_COMPACT`, `STRATA_DEQUANT_SIDE` 等）の個別 ON/OFF 検証。

### B. OS・システムレベルの最適化（未実施 / 見込み +2〜5%）
1. **HugePages (2MB ページ) の有効化 (`scripts/apply_os_tuning.sh`)**
   - これまで `sudo` 未使用のため未適用。55 GB の重みへのランダムアクセスによる TLB ミスを削減し、CPU 側キャッシュミス計算を高速化できる可能性あり。
2. **GPU Persistence Mode・CPU ガバナー `performance`・NUMA/IRQ アフィニティ調整**

### C. エンジン本体（C++ / CUDA）の改造レベル（中〜大改造 / 見込み +3〜10%）
1. **Pコア / Eコア非対称スケジューリング（Work-Stealing / 重み付きチャンク配分）**
   - i9-14900KF の Pコア (3.2 GHz) と Eコア (2.4 GHz) 混在による **Eコア待ち（ストラグラー現象）** の解消。
2. **DFlash2 ドラフトトークンを使った次層エキスパートの先読みプリフェッチ**
   - 検証ステップの裏で、次層で必要になる CPU 側エキスパート計算／転送をさらに深くオーバーラップさせる。
3. **`sm_89` 固有の FP8 (E4M3) Tensor Core 活用**
   - Ada Lovelace の FP8 演算器を活用した量子化・キャッシュ圧縮（大改造）。
4. **サーバー実トラフィックでの PGO / BOLT 最適化**
   - 以前は CLI でプロファイルを取って失敗したが、`--serve` の実トラフィックでプロファイルを採取し直せば改善する可能性はある。

### D. ハードウェア / BIOS 側の最適化（最も効果大 / 見込み +5〜50%+）
1. **RAM の XMP 有効化（DDR5-4000 → DDR5-5600〜6400）** — 見込み **+5〜15%**
   - CPU 側エキスパート計算はメモリ帯域律速のため、温度をほぼ上げずに直結して効く（※4枚差しにするとクロックが落ちるため 2枚差し前提）。
2. **CPU ターボの上限付き解放（3.2 GHz → 4.0〜4.5 GHz キャップ）** — 見込み **+8〜15%**
   - 現在 CPU 46℃ と非常に余裕があるため、電力/クロック上限を控えめに設けて解放すれば 60℃ 前後で安全に高速化可能。
3. **GPU VRAM 増設（RTX 3090 24GB 整備後 / RTX 3090 + RTX 4080 2枚差し = 40GB）** — 見込み **+20〜50%+**
   - Strata は `layer split`（異種 NVIDIA 2枚差し・PCIe x4 でも可）に公式対応。VRAM 40 GB なら Expert Cache ヒット率が 98〜100% に達し、CPU フォールバック自体がほぼ消滅する（※手元の RTX 3090 は `Xid 79` 対策のサーマルパッド・グリス交換および電源容量の確認が必要）。

### E. 運用・ワークフロー側（体感速度アップ）
1. **Prefix Cache（プロンプトキャッシュ）の固定化**: 13K〜17K の Prefill 待ち（4〜6秒）を 0.1 秒未満へ。
2. **モデルルーティング**: 軽いタスクは VRAM 16GB に完全収容できる 8B〜14B モデル（150〜250 tok/s）へ振り分け。

---

## 6. 他の AI に相談する際の質問ポイント例

1. **MoE ハイブリッド推論（VRAM Expert Cache 80% + CPU 20%）のさらなる高速化アイデア**:
   - 上記の既出施策以外で、C++/CUDA レベルまたは Linux OS レベルで見落としている高速化手法はあるか？
2. **Intel ハイブリッド CPU（i9-14900KF, P8+E16, ターボ無効）での CPU 側 MoE 計算の最適化**:
   - スレッド数・アフィニティ・Eコアの扱い（Pコア HT 16スレッドのみ vs P+E 23スレッド vs 重み付き Work-Stealing）でさらに詰められるか？
3. **投機的デコード（DFlash2）の動的ドラフト長・ツリー検証**:
   - 信頼度閾値打ち切り以上に、MoE（キャッシュミスが起きると検証コストが跳ね上がる特性）に特化した「VRAM ヒット率に応じた動的ドラフト長制御」などのアプローチは有効か？
4. **RTX 4080 (16GB) 単体 vs ターボ上限緩和 vs RAM XMP vs 2枚差し（3090+4080）**:
   - 温度・安定性・手間を踏まえた優先順位のセカンドオピニオン。
