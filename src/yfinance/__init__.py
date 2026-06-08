"""Local `yfinance` package wrapper for repository-level screener tools.

This module prefers the system-installed ``yfinance`` package when available
so tools that expect features like ``EquityQuery`` work even when this repo
contains a local ``src/yfinance`` package. The strategy is non-destructive:
- try to import the installed ``yfinance`` by temporarily removing the
  repository ``src`` entry from ``sys.path``;
- if found, expose key attributes (e.g. ``EquityQuery``) and register the
  installed ``yfinance.screener`` in ``sys.modules`` so ``from yfinance.screener``
  resolves to the installed screener;
- otherwise fall back to the local ``screener`` package shipped in this repo.

This keeps the repository runnable and avoids permanently deleting or renaming
the local shim.
"""

from __future__ import annotations

from pathlib import Path
import importlib
import importlib.util
import sys
from types import ModuleType

__all__ = ["screener"]


def _import_installed_yfinance() -> ModuleType | None:
	"""Attempt to import the system-installed ``yfinance`` package.

	This temporarily removes the repository ``src`` directory (the parent of
	this file) from ``sys.path`` to avoid importing the local package.
	Returns the imported module or ``None`` if not available.
	"""
	repo_src = str(Path(__file__).resolve().parents[1])
	saved_module = sys.modules.get("yfinance")
	removed_from_path = False
	mod = None
	try:
		# Temporarily remove local repo src from sys.path so import finds site-packages
		if repo_src in sys.path:
			sys.path.remove(repo_src)
			removed_from_path = True
		# Remove any existing 'yfinance' import so importlib will load the installed one
		if "yfinance" in sys.modules:
			del sys.modules["yfinance"]
		try:
			mod = importlib.import_module("yfinance")
		except Exception:
			mod = None
	finally:
		# restore previous state
		if saved_module is not None:
			sys.modules["yfinance"] = saved_module
		else:
			sys.modules.pop("yfinance", None)
		# Remove any installed yfinance.screener cache that may have been
		# populated while we probed the system package; the repo-local
		# screener package must remain the import target for CLI entry points.
		sys.modules.pop("yfinance.screener", None)
		for name in list(sys.modules):
			if name.startswith("yfinance.screener."):
				sys.modules.pop(name, None)
		if removed_from_path:
			sys.path.insert(0, repo_src)

	# ensure the found module isn't this repo's module
	if mod is not None and hasattr(mod, "__file__"):
		mod_file = str(Path(mod.__file__).resolve())
		if repo_src in mod_file:
			return None
	return mod


# Try to wire up the installed yfinance if present.
_installed: ModuleType | None = None
try:
	_installed = _import_installed_yfinance()
except Exception:
	_installed = None

if _installed is not None:
	# Expose the real yfinance API surface from the installed package so the
	# repo-local shim behaves like yfinance in normal runtime use.
	try:
		# Keep the installed screener available for compatibility, but do not
		# overwrite the repository-local package mapping yet.
		if hasattr(_installed, "screener"):
			screener = _installed.screener  # type: ignore
		else:
			screener = None
		# Mirror the most important top-level helpers used by the screener flow.
		for _name in ("Ticker", "download", "Market", "multi", "base", "cache", "utils", "shared", "exceptions"):
			if hasattr(_installed, _name):
				globals()[_name] = getattr(_installed, _name)
		if hasattr(_installed, "EquityQuery"):
			EquityQuery = getattr(_installed, "EquityQuery")
		if hasattr(_installed, "__version__"):
			__version__ = getattr(_installed, "__version__")
	except Exception:
		# fall back to local-only behavior on any unexpected error
		_installed = None

# Always prefer the repo-local screener package so CLI imports resolve to
# the repository implementation rather than the site-packages shim.
try:
	local_screener_path = Path(__file__).resolve().parent / "screener" / "__init__.py"
	local_screener_spec = importlib.util.spec_from_file_location("yfinance.screener", local_screener_path)
	if local_screener_spec is not None and local_screener_spec.loader is not None:
		_screener = importlib.util.module_from_spec(local_screener_spec)
		sys.modules["yfinance.screener"] = _screener
		local_screener_spec.loader.exec_module(_screener)
		screener = _screener
	else:
		screener = None
except Exception:
	screener = None

# If the installed package wasn't available, fall back to the repo screener.
if _installed is None:
	pass

