# Two stages: configure_file resolves @BIN@, then merge_file folds in the
# translations for Name / Comment / Keywords from po/.
desktop_in = configure_file(
  input: 'de.singular.edith.desktop.in.in',
  output: 'de.singular.edith.desktop.in',
  configuration: conf,
)

desktop_file = i18n.merge_file(
  input: desktop_in,
  output: 'de.singular.edith.desktop',
  type: 'desktop',
  po_dir: meson.project_source_root() / 'po',
  install: true,
  install_dir: datadir / 'applications',
)

metainfo_file = i18n.merge_file(
  input: 'de.singular.edith.metainfo.xml.in',
  output: 'de.singular.edith.metainfo.xml',
  type: 'xml',
  po_dir: meson.project_source_root() / 'po',
  install: true,
  install_dir: datadir / 'metainfo',
)

install_data(
  'de.singular.edith.svg',
  'de.singular.edith-symbolic.svg',
  install_dir: datadir / 'icons' / 'hicolor' / 'scalable' / 'apps',
)
