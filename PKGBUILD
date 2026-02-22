pkgname=edith
pkgver=0.1.2
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
  meson setup builddir --prefix=/usr --buildtype=plain
  ninja -C builddir
}

package() {
  cd "$startdir"
  DESTDIR="$pkgdir" meson install -C builddir
}
