"""Non-destructive photo editing shared by GUI and CLI (no Qt imports).

The optional native extension is loaded only when pixel processing is requested.
"""

from .model import Recipe, AdjustmentStep, EditHistory
from .storage import PhotoEditState, RecipeStore, EditConflict

__all__ = ["Recipe", "AdjustmentStep", "EditHistory", "PhotoEditState", "RecipeStore", "EditConflict"]
