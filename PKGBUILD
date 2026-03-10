pkgname=edith
pkgver=0.4.4
pkgrel=1
pkgdesc="GTK4 native SFTP client for live remote file editing"
arch=('any')
license=('MIT')
depends=(
  'python'
  'python-gobject'
  'gtk4'
  'libadwaita'
  'webkitgtk-6.0'
  'python-paramiko'
  'python-keyring'
  'python-defusedxml'
)
makedepends=('meson' 'ninja' 'npm')
source=()

build() {
  cd "$startdir"
  bash scripts/fetch-monaco.sh
  meson setup build --prefix=/usr --buildtype=plain
  ninja -C build
}

package() {
  cd "$startdir"
  DESTDIR="$pkgdir" meson install -C build
}
