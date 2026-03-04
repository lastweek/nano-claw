"""Slash commands for nano-claw."""

from src.commands.registry import CommandRegistry, Command
from src.commands import builtin
from src.commands.builtin import register_all

__all__ = ['CommandRegistry', 'Command', 'builtin', 'register_all']


