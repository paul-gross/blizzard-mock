"""The forge's HTTP edge — GitHub REST v3-shaped routers plus the lever surface.

Controllers only: each router resolves request inputs and delegates to
``ForgeService`` (``bzh:controller-read-only`` — routers hold the service, never
a store), and ``serialization`` renders the returned domain objects into
vendor-native GitHub JSON so GitHub-shaped client code runs unmodified.
"""
