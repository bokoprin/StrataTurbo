# StrataTurbo

NVIDIA GeForce **RTX 4080 16GB (`sm_89`)** + **Intel Core i9-14900KF**（`no_turbo=1` / 3.2 GHz 固定）環境向けに、[Niko1221/Strata](https://github.com/Niko1221/Strata) (`0.2.18` / engine `0.1.64`) 上で **Qwen3.8-Flash-Next-UD-IQ3_XXS** を限界まで高速化するための専用ビルド・最適化設定・ベンチマーク＆品質検証キットです。

---

## 最終ベンチマーク結果（Baseline `n=3` vs 最終構成 `n=5` 中央値）

| 指標 | Baseline | **最終構成 (`final_max_opt`)** | 改善率 | 備考 |
|---|---:|---:|---:|---|
| **Decode (`decode_code`)** | 90.40 tok/s | **136.70 tok/s** | **+51.2%** | 最大 **140.5 tok/s** |
| **Decode (`decode_ja`)** | 80.10 tok/s | **113.10 tok/s** | **+41.2%** | 最大 **115.3 tok/s** |
| **Decode (`prefill_mid` 4.2K)** | 73.70 tok/s | **103.10 tok/s** | **+39.9%** | |
| **Decode (`prefill_long` 13K)** | 67.50 tok/s | **103.60 tok/s** | **+53.5%** | |
| **Prefill (`prefill_long` 13K)** | 2,217.80 tok/s | **3,438.30 tok/s** | **+55.0%** | 17.6K時 **3,552.5 tok/s** |
| **Expert Cache (VRAM)** | 3,701 slots (7.08 GiB) | **4,770 slots (9.09 GiB)** | **+28.9%** | ヒット率 60–70% → **74–81%** |
| **16問厳密品質テスト** | — | **15/16〜16/16 PASS** | 劣化なし | 厳格版 (`tail_skip=0`) は 15/16 |
| **連続負荷後の温度** | — | **CPU 48℃ / GPU 54℃** | 安全 | `no_turbo=1` (3.2 GHz) 維持 |

詳細な測定データ・各施策の A/B 比較表は [docs/report.md](docs/report.md) を参照してください。

---

## 主な最適化ポイント

1. **VRAM 再配分 (`--vram-reserve-mib 192 --kv k8v4 --kv-resident 20480`)**
   - 空き VRAM と KV バッファを削減し、Expert Cache を 3,701 → 4,770 スロットへ拡張。
2. **RTX 4080 (`sm_89`) 未適用最適化スタックの有効化**
   - 本家 `engine/src/core/nv_defaults.cpp` で `sm_86`/`sm_120` にのみ適用されていた最適化環境変数（`STRATA_ADAPT_NOWAIT=1`, `STRATA_ADAPT_LAG=2`, `STRATA_L2_PF=1`, `STRATA_HC_UPMIX=1`, `STRATA_CPU_IQ_ALL_EXACT1=1` 等）を適用。
3. **長文 Prefill & CPU プール & ルーティング最適化**
   - `STRATA_NV_CHUNK=1`、`--pool-workers 23 --aux-cpus auto`、`STRATA_POOL_SPIN_US=2000`、`STRATA_ROUTE_TAIL_SKIP=7`（16問厳密品質テストで劣化ゼロを確認済み）。
4. **RTX 4080 + Raptor Lake 専用ネイティブビルド**
   - `CMAKE_CUDA_ARCHITECTURES="89-real"` + `-O3 -march=raptorlake -mtune=raptorlake`（AVX-VNNI 有効）。本家ソースコードは改変せず外部ビルドスクリプトで生成するため、本家アップデートへ容易に追従可能。

---

## ディレクトリ構成

```text
StrataTurbo/
├── bench/
│   ├── run_bench.py         # 自動速度ベンチマーク＋Quality Gate
│   ├── eval_quality.py      # 16問厳密品質テスト（C++/Python実行・推論・JSON・8K日本語）
│   └── results/             # 全検証条件の計測結果 JSON
├── configs/
│   ├── baseline/            # 最適化前のプロファイル・systemd unit バックアップ
│   └── final/               # 最終採用プロファイル・systemd unit（APIキーはマスク済み）
├── docs/
│   └── report.md            # 完全検証レポート
└── scripts/
    ├── build_rtx4080.sh     # RTX 4080 (sm_89) + Raptor Lake ネイティブビルド
    ├── update_from_upstream.sh # 本家 Strata アップデート追従＋再ビルド
    ├── apply_config.py      # プロファイル / systemd 設定の安全な切り替え
    ├── rollback_baseline.sh # ベースライン設定への一発ロールバック
    ├── apply_os_tuning.sh   # （任意・要sudo）GPU PL320W / performance governor / HugePages
    └── revert_os_tuning.sh  # OS チューニングの切り戻し
```

---

## 使い方

### 1. RTX 4080 専用ビルド & 本家追従
```bash
git clone --depth 50 https://github.com/Niko1221/Strata.git src/Strata
bash scripts/build_rtx4080.sh

# 本家 Strata のアップデートを取り込んで再ビルドする場合
bash scripts/update_from_upstream.sh
systemctl --user restart strata-llm.service
```

### 2. ベンチマーク・品質テストの実行
API キーは環境変数 `STRATA_API_KEY` で渡してください（コード内にハードコードしていません）：
```bash
export STRATA_API_KEY="your-strata-api-key"
python3 bench/run_bench.py --tag my_test --n 3
python3 bench/eval_quality.py --tag my_qual
```

### 3. 設定のロールバック
```bash
bash scripts/rollback_baseline.sh
```

---

## クレジット & ライセンス

- 本リポジトリは [Niko1221/Strata](https://github.com/Niko1221/Strata) (MIT License) を利用した最適化構成・検証スクリプト集です。
- 本リポジトリのスクリプト・ドキュメントは MIT License の下で公開します。
