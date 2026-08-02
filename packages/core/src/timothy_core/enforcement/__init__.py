"""The enforcement engine's decisions, and the copy that carries them.

Everything here is pure: no database, no Discord, no clock. The workers in phase 3
gather the state, ask these functions what to do, and are responsible for doing it.
"""
