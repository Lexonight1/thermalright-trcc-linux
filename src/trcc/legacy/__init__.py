"""Legacy TRCC code — the original implementation that shipped from
v1.0 through the cutover.  Reachable via ``TRCC_LEGACY=1`` while
next/-as-root is verified on every device.

New work belongs in the top-level ``trcc.*`` packages (the former
``trcc.next.*`` tree, promoted at cutover).  Anything in here is
frozen except for security fixes — eventually deleted.
"""
