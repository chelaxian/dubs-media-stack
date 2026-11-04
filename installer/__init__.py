"""Dubs media stack installer and operations CLI.

Commands: plan, doctor, install, configure, bootstrap, check, update, backup.
Read-only by design where possible; only `install`, `configure`, `bootstrap`,
`update` and `backup` touch the system, and each is idempotent.
"""
__version__ = '1.0.0'
