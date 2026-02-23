configure_file(
  input: '__init__.py.in',
  output: '__init__.py',
  configuration: conf,
  install: true,
  install_dir: moduledir,
)

edith_sources = [
  '__main__.py',
  'application.py',
  'window.py',
]

install_data(edith_sources, install_dir: moduledir)

# Bundled data files
install_data(
  'data' / 'styles' / 'railscasts.xml',
  install_dir: moduledir / 'data' / 'styles',
)

# App-specific icons — kept inside the package data dir; loaded directly
# by path via app_icon() / app_paintable() without touching the icon theme.
install_subdir(
  'data' / 'icons',
  install_dir: moduledir / 'data',
  exclude_files: ['README.md'],
)

gnome.compile_resources(
  'de.singular.edith',
  'data/de.singular.edith.gresource.xml',
  gresource_bundle: true,
  install: true,
  install_dir: moduledir / 'data',
)

subdir('models')
subdir('services')
subdir('widgets')
