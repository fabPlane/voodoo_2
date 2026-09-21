# 32-bit (i386) Linux build environment for the old Glide stack: our sim
# libglide2x, the Glide SDK test programs, and (WIP) Mesa-Glide + GLQuake.
# Everything here is native 32-bit, so the modern host toolchain is untouched.
FROM i386/debian:bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential make git curl ca-certificates python3 \
      zlib1g-dev libx11-dev libxext-dev libxxf86vm-dev \
      verilator xutils-dev \
    && rm -rf /var/lib/apt/lists/*
