# RISC-V bare-metal toolchain (xPack riscv-none-elf-gcc, full multilib incl.
# rv32imafdc/ilp32d) + cmake/make, so the host toolchain is never touched.
FROM debian:trixie-slim
ARG XPACK=15.2.0-1
RUN apt-get update && apt-get install -y --no-install-recommends \
      cmake make xz-utils ca-certificates curl python3 && \
    rm -rf /var/lib/apt/lists/*
RUN curl -fsSL \
      https://github.com/xpack-dev-tools/riscv-none-elf-gcc-xpack/releases/download/v${XPACK}/xpack-riscv-none-elf-gcc-${XPACK}-linux-x64.tar.gz \
      -o /tmp/rv.tar.gz && \
    mkdir -p /opt/rv && \
    tar xf /tmp/rv.tar.gz -C /opt/rv --strip-components=1 && \
    rm /tmp/rv.tar.gz
ENV PATH=/opt/rv/bin:$PATH
