"""TRCC Models — pure data classes with no GUI dependencies.

Split by domain into submodules. This __init__.py re-exports everything
so existing ``from trcc.legacy.core.models import X`` keeps working.
"""
from .api import *  # noqa: F403
from .constants import *  # noqa: F403
from .device import *  # noqa: F403

# Private names used by tests — explicit re-exports
from .device import _VARIANT_REGISTRY as _VARIANT_REGISTRY
from .led import *  # noqa: F403
from .os import *  # noqa: F403
from .overlay import *  # noqa: F403
from .protocol import *  # noqa: F403
from .protocol import _DEFAULT_PROFILE as _DEFAULT_PROFILE
from .protocol import _PM_SUB_TO_FBL as _PM_SUB_TO_FBL
from .protocol import _PM_TO_FBL_OVERRIDES as _PM_TO_FBL_OVERRIDES
from .sensor import *  # noqa: F403
from .theme import *  # noqa: F403
