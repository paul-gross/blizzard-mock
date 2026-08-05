"""The forge's HTTP edge — GitHub REST v3-shaped routers plus the lever surface.

Controllers only: each router resolves inputs and delegates to ``ForgeService``
(``bzh:controller-read-only``); ``serialization`` renders domain objects into
vendor-native GitHub JSON.
"""
