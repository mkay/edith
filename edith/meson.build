edith_sources = [
  '__init__.py',
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
