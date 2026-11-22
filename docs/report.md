# StrataTurbo 最適化完了レポート（ターボ不使用・品質維持・限界最適化）

- **実施日**: 2026-11-22
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
