"""The child-process side of a turn: the ``turn`` verb the service runs each script under, and the helpers bound
into the script's namespace. Nothing here holds session state; every effect a client can see is a request to the
service over the pipe."""
