"""The one-shot Mongo → SQLite import, and the checks that make the cutover reviewable.

This package runs once, by hand, and is then dead. It is written as carefully as the rest
of Timothy anyway, because the thing it produces is the production database and nobody
gets to review it by reading it.

The shape is four steps, deliberately separable so the risky one is small:

1. **`guilds fetch`** asks Discord which guilds Timothy is in and writes a snapshot. The
   only step that touches the network.
2. **`import`** reads a `mongodump` directory and that snapshot, and writes a fresh SQLite
   database. Entirely offline, and therefore repeatable: the same inputs give the same
   output, so a rehearsal is evidence about the real thing.
3. **`verify`** compares the database it produced against the dump it came from — not
   row counts alone, but the set of (guild, user) pairs each side would enforce.
4. **`diff`** reads the audit log of a dry run against the imported data and classifies
   every intended action against what the old bot would have done.

Steps 3 and 4 are the deliverable. The script is just how they get something to check.
"""
