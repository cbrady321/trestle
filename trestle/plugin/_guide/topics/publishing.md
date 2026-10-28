# Publishing

- **Runtime:** `publish_plugin(source=..., name=None)` publishes the source and answers with
  `name` and `registry_version`. Then `describe_plugin(plugin_id=...)` and `run(plugin=...)`.
  Publishing the same name again replaces the plugin.
- **Folder:** the operator lists folders in `[plugins] paths`. Every file there whose name does
  not start with `_` is published at start and on change.
- `list_plugins` shows each plugin's `valid` flag and any `warnings`.

A refusal's code and its fix: `trestle://guide/plugins/refusals`.
