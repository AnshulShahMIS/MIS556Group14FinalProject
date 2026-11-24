"""Distances for display: miles first (US audience), km alongside."""


def dist(km) -> str:
    km = float(km or 0)
    return f"{km * 0.621371:.1f} mi ({km:.1f} km)"
