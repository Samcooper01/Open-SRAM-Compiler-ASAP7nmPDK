"""sramforge - an open-source single-port SRAM (Static Random-Access
Memory) bank generator for the ASAP7 predictive process design kit."""
from .generator import BankSpec, generate

__all__ = ["BankSpec", "generate"]
__version__ = "0.1.0"
