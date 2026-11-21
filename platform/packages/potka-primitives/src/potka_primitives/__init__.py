"""The Pokus Platform's fault primitives and probes.

``potka_primitives.library`` names them; ``potka_primitives.base`` is what
each of them is; ``potka_primitives.context`` is how they reach a run's
targets: through the Incus client, as commands that are lists of arguments
and as file calls, never through a shell.

What the package raises and returns describes an injection. Its errors'
texts are a reason and nothing else; everything more they carry, and every
fact, is for a restricted record and never for a log line.
"""
