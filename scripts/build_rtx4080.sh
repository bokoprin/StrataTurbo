#!/usr/bin/env bash
# Build Strata engine natively for RTX 4080 (sm_89) + i9-14900KF (Raptor Lake / AVX-VNNI)
set -euo pipefail

REPO_DIR="${STRATA_SRC_DIR:-/home/shinsuke/StrataTurbo/src/Strata}"
BUILD_DIR="${STRATA_BUILD_DIR:-/home/shinsuke/StrataTurbo/build/rtx4080}"
CUDA_HOME="${CUDA_HOME:-/usr/local/cuda-12.8}"

export PATH="${CUDA_HOME}/bin:${PATH}"
export LD_LIBRARY_PATH="${CUDA_HOME}/lib64:${LD_LIBRARY_PATH:-}"

cd "$REPO_DIR"

cmake -G Ninja -S engine -B "$BUILD_DIR" \
  -DCMAKE_BUILD_TYPE=Release \
  -DSTRATA_ENABLE_CUDA=ON \
  -DSTRATA_BUILD_TESTS=OFF \
  -DSTRATA_NATIVE_EXPERTS=ON \
  -DSTRATA_PORTABLE=OFF \
  -DSTRATA_Q6K_EXPERTS=ON \
  -DCMAKE_CUDA_ARCHITECTURES="89-real" \
  -DCMAKE_CXX_FLAGS="-O3 -march=raptorlake -mtune=raptorlake" \
  -DCMAKE_C_FLAGS="-O3 -march=raptorlake -mtune=raptorlake"

cmake --build "$BUILD_DIR" --target strata -j "$(nproc)"
echo "Built: $BUILD_DIR/strata"
ls -lh "$BUILD_DIR/strata"
