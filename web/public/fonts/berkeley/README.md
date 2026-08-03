# Berkeley Mono goes here

The industrial theme is designed against Berkeley Mono (U.S. Graphics Company), which is
licensed rather than open, and is therefore **not in this repository and not in any image
built from it** — `.gitignore` and `web/.dockerignore` both exclude it.

It is served from here when it is present, and simply absent when it is not: `styles.css`
lists it first in the industrial font stack with IBM Plex Mono behind it, so a missing
face costs one 404 and nothing else. CI has never had it and never will.

## Locally

Symlink or copy the four web faces in beside this file:

    ln -s ~/berkeley-mono-web/TX-02-*/*.woff2 web/public/fonts/berkeley/

## Deployed

Set `TIMOTHY_BERKELEY_MONO_DIR` in `.env` to an unpacked copy on the host. compose mounts
it read-only over this path in the `web` container. Leave it unset and the deployment
renders the industrial theme in IBM Plex Mono, which is committed under the SIL OFL.
