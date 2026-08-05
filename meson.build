project('edith',
  version: '0.5.0',
  meson_version: '>= 0.62.0',
  default_options: ['warning_level=2'],
)

python = import('python').find_installation('python3')
gnome = import('gnome')
i18n = import('i18n')

gettext_package = meson.project_name()

prefix = get_option('prefix')
bindir = prefix / get_option('bindir')
datadir = prefix / get_option('datadir')

pkgdatadir = datadir / meson.project_name()
moduledir = pkgdatadir / 'edith'
localedir = prefix / get_option('localedir')

conf = configuration_data()
conf.set('PYTHON', python.full_path())
conf.set('VERSION', meson.project_version())
conf.set('pkgdatadir', pkgdatadir)
conf.set('BIN', bindir / meson.project_name())
conf.set('localedir', localedir)

subdir('data')
subdir('edith')
subdir('bin')
subdir('po')

gnome.post_install(
  gtk_update_icon_cache: true,
  update_desktop_database: true,
)

# Ship .pyc alongside the sources: the install prefix isn't user-writable, so
# without this every launch recompiles every module and throws the result away.
meson.add_install_script(python, meson.project_source_root() / 'build-aux' / 'compile-bytecode.py')
