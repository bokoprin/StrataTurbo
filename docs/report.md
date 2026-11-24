# StrataTurbo 最適化完了レポート（ターボ不使用・品質維持・限界最適化）

- **実施日**: 2026-11-22 〜 2026-11-23
- **対象バージョン**: Strata `v0.2.20` (`build 0.1.66` / `rtx4080-turbo` ネイティブビルド + `patches/v0.2.20-cuda-fix.patch`)
- **対象サーバー**: `gwol-target` (`192.168.10.20` / `ILeDEi-R779`)
- **ハードウェア**: NVIDIA GeForce RTX 4080 16GB (`sm_89`) / Intel Core i9-14900KF (`no_turbo=1` 3.2 GHz 固定維持) / 64 GiB DDR5-4000
- **対象モデル**: `Qwen3.8-Flash-Next-UD-IQ3_XXS` + `DFlash2 (q4_0-selq4)`

---

## 1. 最終結果サマリー（Baseline `n=3` vs 最終構成 `final_max_opt` `n=5` 中央値）

| 指標 | Baseline (`n=3`) | **最終構成 (`n=5`)** | 改善率 | 備考 |
|---|---:|---:|---:|---|
| **Decode (`decode_code`)** | 90.40 tok/s | **136.70 tok/s** | **+51.2%** | 最大 **140.5 tok/s** |
| **Decode (`decode_ja`)** | 80.10 tok/s | **113.10 tok/s** | **+41.2%** | 最大 **115.3 tok/s** |
| **Decode (`prefill_mid` 4.2K)** | 73.70 tok/s | **103.10 tok/s** | **+39.9%** | |
| **Decode (`prefill_long` 13K)** | 67.50 tok/s | **103.60 tok/s** | **+53.5%** | |
| **Prefill (`prefill_mid` 4.2K)** | 1,469.20 tok/s | **1,545.10 tok/s** | **+5.2%** | |
| **Prefill (`prefill_long` 13K)** | 2,217.80 tok/s | **3,438.30 tok/s** | **+55.0%** | 17.6K時 **3,552.5 tok/s** |
| **Expert Cache (VRAM)** | 3,701 slots (7.08 GiB) | **4,770 slots (9.09 GiB)** | **+28.9%** | ヒット率 60–70% → **74–81%** |
| **16問厳密品質テスト** | — | **15/16〜16/16 PASS** | 劣化なし | 厳格版 (`tail_skip=0`) は 15/16 |
| **連続負荷後 温度** | — | **CPU 48℃ / GPU 54℃** | 安全 | `no_turbo=1` (3.2 GHz) 維持 |

---

## 2. `STRATA_ROUTE_TAIL_SKIP=7` 厳密品質 A/B 比較結果

C++ コンパイル＆実行テスト（4問）、Python 単体テスト（4問）、数学・論理推論（4問、`reasoning_effort=medium`）、厳密 JSON スキーマ＆日本語 8K 長文針探し・制約付き要約（4問）の計 16 問（`eval_quality.py`）で比較しました。

| 条件 | 合計 PASS | C++ (4) | Python (4) | 推論 (4) | JSON/日本語8K (4) | 中央値 Decode |
|---|---:|---:|---:|---:|---:|---:|
| **厳格版 (`tail_skip=0`, `qual_strict`)** | **15/16 (93.8%)** | 4/4 | 3/4 (`B4_py_expr` FAIL) | 4/4 | 4/4 | 118.4 tok/s |
| **`STRATA_ROUTE_TAIL_SKIP=7` (`qual_ts7`)** | **16/16 (100.0%)** | 4/4 | 4/4 | 4/4 | 4/4 | 147.2 tok/s |
| **最終構成 (`final_max_opt_qual`)** | **15/16 (93.8%)** | 4/4 | 3/4 (`B4_py_expr` FAIL) | 4/4 | 4/4 | 148.05 tok/s |

- **結論**: `STRATA_ROUTE_TAIL_SKIP=7` による品質劣化は確認されず、正答率（93.8%〜100%）は厳格版と同等以上であったため、**`STRATA_ROUTE_TAIL_SKIP=7` を正式採用**しました。

---

## 3. 追加最適化の検証結果（ターボ不使用）

1. **RTX 4080 (`sm_89`) 未適用最適化スタック（採用）**
   - `nv_defaults.cpp` で `sm_86`/`sm_120` 向けに用意されていた環境変数群をすべて適用（`STRATA_ADAPT_NOWAIT=1`, `STRATA_ADAPT_LAG=2`, `STRATA_L2_PF=1`, `STRATA_HC_UPMIX=1`, `STRATA_CPU_IQ_ALL_EXACT1=1` 等）。
2. **VRAM リザーブ限界調整（採用: `192 MiB`）**
   - `--vram-reserve-mib 192 --kv k8v4 --kv-resident 20480` により **4,770 スロット (9.09 GiB)** まで拡張。17.6K 長文ストレステストも 6.32 秒で完走。
   - ※他 GPU アプリや Vision 同時利用で窮屈な場合は `256 MiB`（4,738 スロット）へ戻すことで余裕を確保可能。
3. **未探索カーネルノブ `k1`〜`k6`（不採用）**
   - `STRATA_GR_V3=1`, `STRATA_FUSED_SWIGLU_Q81=1`, `STRATA_ATTN_V3=1`, `STRATA_L2_PF_BLOCKS=16/24`, `STRATA_POOL_UMWAIT=1` を個別検証したが、総合速度で `t5` を上回らなかったため不採用。
4. **カスタム `expert-profile-custom.bin`（不採用）**
   - 混合ワークロードから再学習生成して `n=3` で比較したが、元の `expert-profile.bin`（`decode_ja` 113.7 vs 107.5 tok/s）が上回ったため元プロファイルを維持。
5. **PGO ビルド（不採用）**
   - CLI 生成プロファイルを用いた 2-stage PGO ビルドを検証したが、サーバーループのホットパスが十分反映されず `decode_code` 125.1 → 113.2 tok/s に低下したため不採用。通常の `-O3 -march=raptorlake -mtune=raptorlake` + `sm_89` ネイティブビルド（`/home/shinsuke/StrataTurbo/build/rtx4080/strata`）を採用。

---

## 4. ロールバック・運用コマンド

- **元のベースライン設定へ一発復旧**:
  ```bash
  /home/shinsuke/StrataTurbo/scripts/rollback_baseline.sh
  ```
- **任意で `STRATA_ROUTE_TAIL_SKIP=7` を外す場合**:
  `/home/shinsuke/.config/systemd/user/strata-llm.service` から `Environment=STRATA_ROUTE_TAIL_SKIP=7` を削除し、`systemctl --user daemon-reload && systemctl --user restart strata-llm.service` を実行。
- **本家 Strata アップデート追従**:
  ```bash
  bash /home/shinsuke/StrataTurbo/scripts/update_from_upstream.sh && systemctl --user restart strata-llm.service
  ```
- **（任意）sudo OS チューニング（GPU Persistence / PL320W / HugePages）**:
  ```bash
  sudo bash /home/shinsuke/StrataTurbo/scripts/apply_os_tuning.sh
  ```

---

## 5. Strata `v0.2.19` (`engine 0.1.65`) アップデート検証結果

本家 `Niko1221/Strata` `v0.2.19` では `nv_defaults.cpp` に Ada Lovelace (`sm_89`) のネイティブ最適化テーブル（`STRATA_DEQUANT_SIDE=1`, `STRATA_QFUSE=1`, `STRATA_LFUSE=1`, `STRATA_PLE_COMPACT=1`, `STRATA_PLE_COPY_STREAM=1`, `STRATA_PF_FUSED_KQ=1` 等）が正式追加されました。Control (`0.1.65`) および RTX 4080 専用ビルド (`engine 0.1.65`) へ更新し、動作確認と計測を実施しました。

| 条件 | Slots | `decode_code` | `decode_ja` | `prefill_mid` (D / P) | `prefill_long` (D / P) | 16問品質テスト |
|---|---:|---:|---:|---:|---:|---|
| `v0.2.18` (`final_max_opt`, `n=5`) | 4,770 | 136.70 | 113.10 | 103.10 / 1,545.1 | 103.60 / 3,438.3 | 15/16 (93.8%) |
| `v0.2.19` 整理前 (`v0219_confirm`, `n=3`) | 4,654 | 125.90 | 109.80 | 101.10 / 1,542.5 | 105.50 / 3,449.0 | 16/16 (100.0%) |
| **`v0.2.19` 整理後 (`v0219_clean_sm89`, `n=5`)** | **4,654** | **128.00** | **110.70** | **101.10 / 1,542.5** | **103.30 / 3,429.0** | — |
| `v0.2.19` PLE無効 (`v0219_clean_nople`, `n=3`) | 4,654 | 123.70 | 110.80 | 98.70 / 1,542.6 | 102.90 / 3,442.8 | — |

- `v0.2.19` では公式 `sm_89` デフォルトに重複する手動 `sm_86` 環境変数を外した `v0219_clean_sm89` が最速（`decode_code` 128.00 tok/s / `decode_ja` 110.70 tok/s）となり、16問厳密品質テストは **16/16 (100.0%)** を達成。サービスは `active` / `/health` `loaded: true` で正常稼働中です。

---

## 6. Strata `v0.2.20` (`engine 0.1.66`) バグ修正＆追従検証結果

### 6.1 本家 `v0.2.20` の CUDA バグ原因と修正パッチ
- **事象**: `v0.2.20`（公式 `0.1.66` バイナリおよび自前ビルドの両方）で初回推論ウォームアップ時に `verify: layer 0 never rang (operation not supported on global/shared address space)` で異常終了。
- **原因**: `compute-sanitizer --tool memcheck` により、`engine/src/kernels/cuda/fused_gr.cu` で新設された `gr_up_q8_tc_kernel`（および `gr_down_q8_tc_kernel`）が、`__shared__` メモリ上の重みバッファ `s_w` に対してグローバルメモリ専用の `__ldg()` を使う `q8_ld16()` を呼んでいたことを特定。
- **修正**: `q8_lds16()`（通常の shared memory ロード）を追加して `gr_down_q8_tc_kernel` / `gr_up_q8_tc_kernel` 内の `s_w` 読み出しを置き換え（[patches/v0.2.20-cuda-fix.patch](../patches/v0.2.20-cuda-fix.patch)）。

### 6.2 短文・中文・長文 速度測定結果（`v0.2.19` 比）

| 条件 | Slots | `decode_code` (短文) | `decode_ja` (短文) | `prefill_mid` (4.2K, D / P) | `prefill_long` (13K, D / P) | 17.6K 長文 (D / P) | 16問品質 |
|---|---:|---:|---:|---:|---:|---:|---|
| `v0.2.19` (`v0219_clean_sm89`, `n=5`) | 4,654 | 128.00 | 110.70 | 101.10 / 1,542.5 | 103.30 / 3,429.0 | — | 16/16 |
| **`v0.2.20` 修正版 (`v0220_fixed_bench`, `n=3`)** | **4,654** | **121.60** *(最大 132.3)* | **105.60** *(最大 107.0)* | **101.90 / 1,632.1** | **101.60 / 3,420.9** | **94.1 / 3,552.6** | **16/16 (100%)** |

- **16問厳密品質テスト (`v0220_fixed_qual`)**: **16/16 PASS (100.0%)**、中央値 Decode **142.1 tok/s**（最大 **155.6 tok/s**）。
- **温度・VRAM**: CPU **50.0℃** / GPU **54.0℃**、負荷時空き VRAM **185 MiB**（従来より約 180 MiB 余裕増）。
- **特筆**: `prefill_mid` (4.2K) の Prefill が 1,542.5 → **1,632.1 tok/s (+5.8%)** へ向上し、Expert Cache ヒット率も 79〜84% に上昇。

### 6.3 `v0.2.20` で見つかった今後の高速化余地（未実施・調査メモ）
1. **VRAM 空き余裕の活用（`--vram-reserve-mib 192 → 96〜128`）**: `v0.2.20` では連続負荷時でも VRAM が 185 MiB 余っているため、リザーブをさらに詰めてキャッシュスロットを増やす余地あり。
2. **`#4870` 適応キャッシュ / ピン留め率 (`STRATA_CACHE_PIN_FRAC`)**: デフォルトで上位 25%（1,163 スロット）を追い出し禁止にし、スワップ頻度も倍増（4→2）しているため、短文 Decode がわずかに落ちた要因になっていないか `STRATA_CACHE_PIN_FRAC=0` 等で検証する余地あり。
3. **新設 Tensor Core モード (`STRATA_HC_Q8_TC=2` / `STRATA_HC_Q8_BF16TC=1`)**: `gr_down_q8_tc_kernel` 併用時の A/B 比較余地あり。
4. **CPU ワーカー数再検証（`--pool-workers 23` vs `15`/`19`）**: `v0.2.20` で CPU `IQ3_XXS` カーネルが高速化されたため、最適ワーカー数が変わった可能性あり。


### 6.4 v0.2.20 高速化チューニング（`opt1`〜`opt6` スクリーニング & `v0220_opt_best`）

セクション 6.3 の4項目について、`v0.2.20` (`build/rtx4080/strata`) 上で `n=2` の A/B スクリーニングを実施しました。

| Tag | 変更内容（ベースライン比） | `decode_code` | `decode_ja` | `prefill_mid` (dec / pref) | `prefill_long` (dec / pref) | Expert Slots | 備考 |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | :--- |
| `v0220_fixed_bench` (`n=3`) | ベースライン (`reserve=192`, `workers=23`) | 121.60 | 105.60 | 101.90 / 1632.10 | 101.60 / 3420.90 | 4654 (8.87 GiB) | 基準 |
| `opt1_res96` (`n=2`) | `--vram-reserve-mib 96` | 119.65 | 106.65 | 100.70 / 1624.90 | **107.15 / 3442.20** | **4704 (8.97 GiB)** | +50 slots、長文改善 |
| `opt2_nopin` (`n=2`) | `STRATA_CACHE_PIN_FRAC=0` | **126.05** | **108.05** | **102.45 / 1628.75** | 97.75 / 3422.45 | 4654 (8.87 GiB) | **短文 decode +3.7%** |
| `opt3_hctc2` (`n=2`) | `STRATA_HC_Q8_TC=2` | 116.40 | 108.15 | 105.90 / 1628.40 | 103.25 / 3421.15 | 4654 (8.87 GiB) | `decode_code` 低下 |
| `opt4_hctc0_bf16` (`n=2`) | `STRATA_HC_Q8_TC=0` | - | - | - | - | - | HTTP 503 で不採用 |
| `opt5_w15` (`n=2`) | `--pool-workers 15` | 115.40 | 101.45 | 100.00 / 1600.45 | 100.20 / 3410.50 | 4654 (8.87 GiB) | 23 workers より低下 |
| `opt6_w19` (`n=2`) | `--pool-workers 19` | 117.80 | 107.40 | 100.25 / 1606.70 | 106.20 / 3418.15 | 4654 (8.87 GiB) | `decode_code` 低下 |

効果の高かった **`--vram-reserve-mib 96`**（`opt1_res96`）と **`STRATA_CACHE_PIN_FRAC=0`**（`opt2_nopin`）を組み合わせ、`--pool-workers 23` を維持した **`v0220_opt_best`** で本計測（`n=5`）・17.6K ストレステスト・16問品質テストを実施しました。

- **本計測 `v0220_opt_best` (`n=5` 中央値)**:
  - `decode_code`: **129.70 tok/s**（`121.60` → `+6.7%`、iter 5 最大 `145.7 tok/s`）
  - `decode_ja`: **109.40 tok/s**（`105.60` → `+3.6%`）
  - `prefill_mid`: decode **101.90 tok/s** / prefill **1644.30 tok/s**
  - `prefill_long`: decode **109.20 tok/s**（`101.60` → `+7.5%`） / prefill **3438.40 tok/s**
  - `expert_cache_slots`: **4704 (8.97 GiB)**、Quality Gate: **PASS**
- **17.6K 長文ストレステスト**: `STRESS_OK`（`prompt_n=16731`, `predicted_n=128`, `elapsed=5.99s`, `prefill=3583.50 tok/s`, `decode=99.30 tok/s`, VRAM 残り `89 MiB` / OOM なし）
- **16問品質評価 (`v0220_opt_qual`)**: **15/16 PASS (93.8%)**, `median_dec=146.6 tok/s`（`B4_py_expr` のみ Python 負数除算丸め差異）
- **温度（`no_turbo=1` 維持）**: CPU `x86_pkg_temp=50.0°C`, GPU `55°C`

---

## 7. Strata `v0.2.21` (`engine 0.1.67` / Control `0.1.67`) 追従結果

- **変更点**: `v0.2.20` で独自に当てていた `fused_gr.cu` の CUDA shared memory ロード修正（`q8_smem_ld16`）が本家 `v0.2.21` に取り込まれたため、パッチ不要のクリーンな本家ソース (`v0.2.21`) で `build/rtx4080/strata` を再ビルドしました。Control も `0.1.67`（`strata 0.2.21`）へ更新済みです。
- **速度測定 (`v0221_opt_bench`, `n=3` 中央値)**:
  - `decode_code`: **128.30 tok/s** *(最大 129.3)* / prefill **152.70 tok/s**
  - `decode_ja`: **110.50 tok/s** *(最大 112.5)* / prefill **181.30 tok/s**
  - `prefill_mid` (4.2K): decode **101.60 tok/s** / prefill **1643.20 tok/s**
  - `prefill_long` (13K): decode **101.90 tok/s** / prefill **3431.60 tok/s**
  - `expert_cache_slots`: **4704 (8.97 GiB)**、Quality Gate: **PASS**
- **17.6K 長文ストレステスト**: `STRESS_OK` (`prompt_n=16731`, `predicted_n=128`, `elapsed=5.97s`, `prefill=3536.10 tok/s`, `decode=106.60 tok/s`, VRAM 残り `89 MiB` / OOM なし)
- **16問厳密品質評価 (`v0221_opt_qual`)**: **16/16 PASS (100.0%)**, `median_dec=151.15 tok/s`
- **温度（`no_turbo=1` 維持）**: CPU `x86_pkg_temp=46.0°C`, GPU `51°C`
