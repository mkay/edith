pkgname=edith
pkgver=0.1.4
pkgrel=1
pkgdesc="GTK4 native SFTP client for live remote file editing"
arch=('any')
license=('GPL-3.0-or-later')
depends=(
  'python'
  'python-gobject'
  'gtk4'
  'libadwaita'
  'gtksourceview5'
  'python-paramiko'
  'python-keyring'
)
makedepends=('meson' 'ninja')
source=()

build() {
  cd "$startdir"
  meson setup build --prefix=/usr --buildtype=plain
  ninja -C build
}

package() {
  cd "$startdir"
  DESTDIR="$pkgdir" meson install -C build
}
