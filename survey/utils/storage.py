GB = 1024 ** 3


def format_bytes(octets: int) -> str:
    """Affiche une taille lisible (o, Ko, Mo, Go)."""
    octets = int(octets or 0)
    if octets < 1024:
        return f"{octets} o"
    if octets < 1024 ** 2:
        return f"{round(octets / 1024, 1)} Ko"
    if octets < GB:
        return f"{round(octets / (1024 ** 2), 1)} Mo"
    return f"{round(octets / GB, 2)} Go"


def go_to_bytes(go: int) -> int:
    return int(go) * GB


def pourcentage(octets_utilises: int, quota_octets: int) -> float:
    if quota_octets <= 0:
        return 0.0
    return min(100.0, round((octets_utilises / quota_octets) * 100, 1))
