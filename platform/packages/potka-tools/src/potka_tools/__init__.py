"""The Pokus Platform's tool registry.

``potka_tools.registry`` reads the registry: the tools a model may call,
each with its arguments and the command or handler behind it. The package
runs nothing and reaches nothing: what it reads is handed to the service
that executes a call.
"""
