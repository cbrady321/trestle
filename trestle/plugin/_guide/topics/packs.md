# Packs

A pack is a library plus a folder of thin `@trestle` wrapper files that call it:

1. Ship the library as a normal Python distribution. The operator installs it into the
   service's own environment.
2. Write one wrapper file per tool. Each declares the library with
   `@trestle(packages=["your_pack"])`.
3. The operator adds the wrapper folder to `[plugins] paths` and restarts the service. Trestle
   never loads plugins from a package's entry points.
4. Unit-test the wrappers with `trestle.plugin.testing`.
5. After a library upgrade, wrappers stop with `execution.provenance_mismatch` until they are
   published again (a restart republishes the folder).

See `trestle://guide/examples/pack_wrapper`.
