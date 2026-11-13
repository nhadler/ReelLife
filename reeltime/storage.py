"""How collected static files are stored and named."""

from whitenoise.storage import CompressedManifestStaticFilesStorage


class StaticStorage(CompressedManifestStaticFilesStorage):  # type: ignore[misc]
    """WhiteNoise's storage, with ES module imports by hashed name.

    Pages load scripts by their hashed names, which browsers cache for
    good. Rewriting each `import … from './x.js'` to the hashed name too
    means a page load fetches no module again; under the plain names the
    browser asked again for every imported module on each load, and one
    dropped request left the module and everything importing it unrun.
    """

    support_js_module_import_aggregation = True
