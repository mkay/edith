desktop_file = configure_file(
  input: 'com.github.edith.desktop.in',
  output: 'com.github.edith.desktop',
  configuration: conf,
  install: true,
  install_dir: datadir / 'applications',
)

install_data(
  'com.github.edith.svg',
  install_dir: datadir / 'icons' / 'hicolor' / 'scalable' / 'apps',
)
