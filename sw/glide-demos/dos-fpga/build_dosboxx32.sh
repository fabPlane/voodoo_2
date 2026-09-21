#!/usr/bin/env bash
# Build a 32-bit DOSBox-X from source on x86_64 Ubuntu, wiring up i386 SDL2
# without the conflicting :i386 -dev metapackages. Produces src/dosbox-x
# (ELF 32-bit) usable as DOSBOXX32_BIN for capture_dos_glide.sh.
#
# Alternative (simpler if you have nix): nix build nixpkgs#pkgsi686Linux.dosbox-x
set -euo pipefail

sudo dpkg --add-architecture i386
sudo apt-get update -qq
# 32-bit toolchain + runtime libs (dev headers are arch-independent from amd64).
sudo apt-get install -y gcc-multilib g++-multilib automake autoconf libtool \
  libsdl2-2.0-0:i386 libsdl2-net-2.0-0:i386 \
  libpng-dev:i386 zlib1g-dev:i386 libxkbfile-dev:i386 \
  libsdl2-dev libsdl2-net-dev libpng-dev

# i386 link symlinks + a stub libiconv (glibc provides iconv, DOSBox-X asks for -liconv).
cd /usr/lib/i386-linux-gnu
sudo ln -sf libSDL2-2.0.so.0 libSDL2.so
sudo ln -sf libSDL2_net-2.0.so.0 libSDL2_net.so
echo 'void _iconv_stub(void){}' | gcc -m32 -shared -xc - -o /tmp/libiconv.so
sudo cp /tmp/libiconv.so /usr/lib/i386-linux-gnu/libiconv.so

# i386 pkg-config files (copy amd64 .pc, rewrite the libdir), + i386 SDL_config.h.
sudo mkdir -p /usr/lib/i386-linux-gnu/pkgconfig
for pc in sdl2 SDL2_net; do
  sudo sed 's#x86_64-linux-gnu#i386-linux-gnu#g' \
    /usr/lib/x86_64-linux-gnu/pkgconfig/$pc.pc \
    | sudo tee /usr/lib/i386-linux-gnu/pkgconfig/$pc.pc >/dev/null
done
cd /tmp && apt-get download libsdl2-dev:i386
dpkg-deb -x libsdl2-dev_*_i386.deb /tmp/sdl2i386
sudo mkdir -p /usr/include/i386-linux-gnu/SDL2
sudo cp -r /tmp/sdl2i386/usr/include/i386-linux-gnu/SDL2/* /usr/include/i386-linux-gnu/SDL2/

# Build DOSBox-X (-m32 -msse2, i386 pkg-config).
cd /tmp && rm -rf dosbox-x
git clone --depth 1 https://github.com/joncampbell123/dosbox-x.git
cd dosbox-x && ./autogen.sh
PKG_CONFIG_LIBDIR=/usr/lib/i386-linux-gnu/pkgconfig \
  CC='gcc -m32 -msse2' CXX='g++ -m32 -msse2' LDFLAGS='-L/usr/lib/i386-linux-gnu' \
  ./configure --enable-sdl2 --prefix=/tmp/dbx
make -j"$(nproc)"
file -Lb src/dosbox-x
echo "Built: /tmp/dosbox-x/src/dosbox-x  (use as DOSBOXX32_BIN)"
