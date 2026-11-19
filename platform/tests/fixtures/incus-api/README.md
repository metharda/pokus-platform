# The Incus API extract

`api.json` is an extract of two files of [Incus](https://github.com/lxc/incus),
made for the tests of the platform's Incus client and of its in-memory
Incus: they compare every request and every response with it.

| | |
| --- | --- |
| Repository | `https://github.com/lxc/incus` |
| Release | `v6.0.7` |
| Commit | `7678ccc46b2d0b8092b2b9419ab6255e3ca98f00` |
| `doc/rest-api.yaml` | sha256 `f5648960d865984b9100cc46105e6b7b3f9259c3118683c25ddc61623043151e` |
| `internal/server/metadata/configuration.json` | sha256 `12f35177d58b63e999fe23033b5f9e39b316473e7ac4a10fefc7e7e6d649bbc9` |

`source.json` holds the same facts for the tools.

## What the extract is

It is not a copy of either file. From `doc/rest-api.yaml`, the description
of Incus's REST API, it keeps, for the calls the client makes and nothing
else: the names of each call's query parameters and headers, the response
codes, and the structure (types, properties, items, references) of the
request and response bodies and of the definitions they name. From
`configuration.json` it keeps the names of the configuration keys of an
instance and of two device types. Example values, descriptions and
contact details are left out.

`make incus-sources INCUS=<directory>` fetches the two files from the
commit above and refuses one whose sha256 differs; `make incus-api
INCUS=<directory>` writes the extract from them. Where the two files are
at hand (`make generated` with `INCUS=<directory>`, and always in CI) `api.json`
is compared with a fresh extract, so it is never edited by hand.

## Licence

Incus is licensed under the Apache License, Version 2.0, as this repository
is (`LICENSE` at its root holds the licence's text); copyright belongs to
the Incus authors (`AUTHORS` in Incus's repository). The extract is a
derived work under the same licence, changed as described above.
