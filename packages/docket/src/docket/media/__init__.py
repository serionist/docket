"""Media: a record's audio or file, put through the gate and encrypted per file (DR-0547 to DR-0551).

| Module | What |
|---|---|
| `layout` | where media waits and is stored, its sidecar, a media file's family, the type from the first bytes |
| `crypto` | age through pyrage: a key per file, encryption while reading, the header's key id |
| `keystore` | this clone's keys, sealed in its private folder; the seal in the keychain, or a file by opt-in |
| `fetch` | a path under a media folder or the drop folder, or a URL over https with every address checked |
| `screen` | the four checks a media file passes at the put, at every sweep and at promotion |
| `staged` | staged media beside its record: the withheld marks, deleting with a drop, orphans, promotion |
| `put` | `media_put`: the checks, the fetch, the checks again, then the file into staging |
| `reader` | the one reader: the bytes in memory, or why not |
| `sources` | a source's media hosts and folders in vault.yaml: adding asks at the console, removing doesn't |
| `status` | `docket media status`, by counts |

Nothing here writes a plain copy of a media file, logs a URL or names a dropped record.
"""
