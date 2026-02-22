desktop_file = configure_file(
  input: 'de.singular.edith.desktop.in',
  output: 'de.singular.edith.desktop',
  configuration: conf,
  install: true,
  install_dir: datadir / 'applications',
)

install_data(
  'de.singular.edith.svg',
  'de.singular.edith-symbolic.svg',
  install_dir: datadir / 'icons' / 'hicolor' / 'scalable' / 'apps',
)
