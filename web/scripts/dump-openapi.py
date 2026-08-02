"""Write the backend's OpenAPI document to stdout.

The generated client is only as correct as the schema it was generated from, so this
builds the schema from the *application* rather than from a running container. No
network, no database, no Discord: `create_app` wires all of that in its lifespan, and
`openapi()` never enters it.

Run through `npm run api`, which invokes it with `uv run --project ..` so the workspace's
own `timothy-api` is importable, and pipes the result into `openapi-typescript`.
"""

import json
import sys

from timothy_api.app import create_app

sys.stdout.write(json.dumps(create_app().openapi(), indent=2))
