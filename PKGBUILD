# Maintainer: C0ldbloodthe0ry <joshuafricke@gmail.com>
pkgname=cachycompanion-git
pkgver=r1.0000000
pkgrel=1
pkgdesc="Lightweight system monitor + remote task-manager daemon for Arch Linux, with an Android companion app (git version)"
arch=('any')
url="https://github.com/C0ldbloodthe0ry/cachycompanion"
license=('MIT')
depends=('bash' 'python' 'python-psutil')
makedepends=('git')
optdepends=(
  'qrencode: QR-code pairing with the phone app'
  'android-tools: push/update the companion app on a USB-connected phone'
  'android-udev: USB permissions for adb, only needed for the push flow'
  'nvidia-utils: GPU stats on NVIDIA hardware, via nvidia-smi'
)
provides=('cachycompanion')
conflicts=('cachycompanion')
source=("$pkgname::git+$url.git")
sha256sums=('SKIP')

pkgver() {
  cd "$pkgname"
  printf "r%s.%s" "$(git rev-list --count HEAD)" "$(git rev-parse --short HEAD)"
}

package() {
  cd "$pkgname"

  # daemon lives read-only under /usr/lib; its writable config lives in the user's
  # XDG config dir instead (cachycompanion.py falls back there automatically when
  # there's no co-located config.json next to the script)
  install -Dm755 cachycompanion.py "$pkgdir/usr/lib/cachycompanion/cachycompanion.py"

  # data the manager script needs at runtime (APK to push, example config to seed
  # a fresh ~/.config/cachycompanion/config.json from)
  install -Dm644 config.example.json "$pkgdir/usr/share/cachycompanion/config.example.json"
  install -Dm644 cachycompanion.apk "$pkgdir/usr/share/cachycompanion/cachycompanion.apk"

  install -Dm755 cachycompanion-manager.sh "$pkgdir/usr/bin/cachycompanion-manager"

  # self-contained installs point ExecStart at %h/cachycompanion/...; a package
  # install has one fixed system path instead, so rewrite it at build time rather
  # than maintaining a second near-duplicate unit file
  install -d "$pkgdir/usr/lib/systemd/user"
  sed 's|%h/cachycompanion/cachycompanion\.py|/usr/lib/cachycompanion/cachycompanion.py|' \
    cachycompanion.service > "$pkgdir/usr/lib/systemd/user/cachycompanion.service"

  install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
  install -Dm644 README.md "$pkgdir/usr/share/doc/$pkgname/README.md"
}
