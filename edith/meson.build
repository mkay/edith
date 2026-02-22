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

subdir('models')
subdir('services')
subdir('widgets')
