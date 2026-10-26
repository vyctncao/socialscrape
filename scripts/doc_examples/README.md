# Docs examples

`socialscrape/data/doc_examples.json` holds the request and response examples shown beside each endpoint in the
dashboard docs (served by `GET /meta/examples`). They are ScrapeCreators' own examples, in this server's envelope.
These scripts rebuild the file from ScrapeCreators' public docs.

Where the data comes from:
- Each docs page has a markdown twin (`/a/b.md`, or `/a/b/post.md` for a POST page). It embeds the operation's
  OpenAPI YAML, with the current 200 example and the parameter examples. That 200 example is newer than the one
  in `openapi.json`.
- The site's JS bundle (`/assets/index-*.js`) holds the trimmed samples and some endpoint-specific error messages.
  They are minified object literals: `extract_bundle.mjs` parses them with acorn and never evaluates the bundle.

Downloads are untrusted data. Keep them in a scratch directory outside the repo, and read them with `python -I`.

```sh
WORK=$(mktemp -d)   # scratch: downloads, the bundle extract and the build report

# 1. The operations to cover. The catalog is computed at run time, so dump it rather than parse catalog.py.
.venv/bin/python -c "import json; from socialscrape.catalog import CATALOG; print(json.dumps(CATALOG))" \
    > $WORK/catalog.json

# 2. Every operation's .md twin, fetched one at a time. Pages already fetched are skipped.
python3 -I scripts/doc_examples/fetch_pages.py $WORK/catalog.json $WORK/raw

# 3. The bundle: its name is in any page's HTML (<script type="module" src="/assets/index-XXXX.js">).
curl -sSL -o $WORK/bundle.js https://docs.scrapecreators.com/assets/index-XXXX.js
(cd scripts/doc_examples && npm install --no-save)
node scripts/doc_examples/extract_bundle.mjs $WORK/bundle.js $WORK/bundle_endpoints.json   # must say "unresolved 0"

# 4. The OpenAPI document, used where a page has no example.
curl -sSL -o $WORK/openapi.json https://docs.scrapecreators.com/openapi.json

# 5. Build, then validate (operation keys, envelopes, catalog params, contract conformance).
.venv/bin/python -I scripts/doc_examples/build_doc_examples.py --catalog $WORK/catalog.json \
    --pages $WORK/raw/pages --bundle $WORK/bundle_endpoints.json --spec $WORK/openapi.json \
    --out socialscrape/data/doc_examples.json --report $WORK/build_report.json
.venv/bin/python -I scripts/doc_examples/validate_doc_examples.py . $WORK/bundle_endpoints.json
```

Then run `tests/test_examples.py`. Review the diff of `doc_examples.json` like any upstream contract change.
