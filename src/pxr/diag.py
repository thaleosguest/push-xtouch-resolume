"""Diagnostic de l'accès à Arena (pxr --check, menu « Diagnostic » de l'icône) : moins de 5 s au total.

Étapes : nom d'hôte -> port TCP du webserver -> API REST (/api/v1/product) -> WebSocket (composition reçue).
Chaque étape qui échoue donne un indice. L'OSC n'est pas testé : la passerelle n'utilise que le webserver.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import time
from dataclasses import dataclass

import httpx
import websockets


@dataclass
class Step:
    name: str
    ok: bool | None          # None = non testé (étape précédente en échec)
    detail: str
    hint: str = ""


def _network_kind(ip: str) -> str:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return ""
    if a.is_loopback:
        return "cette machine"
    if a in ipaddress.ip_network("100.64.0.0/10"):
        return "Tailscale"
    if a.is_link_local:
        return "lien local 169.254 (pas de DHCP : préférer une IP fixe)"
    if a.is_private:
        return "réseau local"
    return "réseau public"


async def run_check(host: str, port: int, budget_s: float = 4.5) -> list[Step]:
    t0 = time.perf_counter()
    left = lambda: max(0.3, budget_s - (time.perf_counter() - t0))   # noqa: E731
    steps: list[Step] = []

    # 1. résolution du nom
    try:
        infos = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM), 1.5)
        ip = infos[0][4][0]
        kind = _network_kind(ip)
        steps.append(Step("Nom d'hôte", True, f"{host} -> {ip}" + (f" ({kind})" if kind else "")))
    except (OSError, asyncio.TimeoutError) as e:
        steps.append(Step("Nom d'hôte", False, f"{host} : introuvable ({e})",
                          "vérifie l'adresse dans config.yaml (arena.host) ; une IP fixe évite ce problème"))
        return steps + [Step(n, None, "non testé") for n in ("Port du webserver", "API REST", "WebSocket")]

    # 2. port TCP
    t = time.perf_counter()
    try:
        _, w = await asyncio.wait_for(asyncio.open_connection(ip, port), min(2.0, left()))
        w.close()
        steps.append(Step("Port du webserver", True, f"{ip}:{port} ouvert ({(time.perf_counter() - t) * 1000:.0f} ms)"))
    except ConnectionRefusedError:
        steps.append(Step("Port du webserver", False, f"{ip}:{port} refusé",
                          "Arena est-il lancé ? Webserver activé (Preferences > Webserver) ? Bon port ?"))
        return steps + [Step(n, None, "non testé") for n in ("API REST", "WebSocket")]
    except (OSError, asyncio.TimeoutError) as e:
        steps.append(Step("Port du webserver", False, f"{ip}:{port} sans réponse ({type(e).__name__})",
                          "machine éteinte, câble, mauvais port (Arena > Preferences > Webserver), ou pare-feu de "
                          f"la machine d'Arena qui bloque le port TCP {port} (règle entrante, profil Privé)"))
        return steps + [Step(n, None, "non testé") for n in ("API REST", "WebSocket")]

    # 3. REST
    try:
        async with httpx.AsyncClient(timeout=min(1.5, left())) as c:
            r = await c.get(f"http://{host}:{port}/api/v1/product")
        p = r.json()
        steps.append(Step("API REST", True, f"{p.get('name')} {p.get('major')}.{p.get('minor')}.{p.get('micro')}"))
    except Exception as e:
        steps.append(Step("API REST", False, f"pas de réponse REST valide ({type(e).__name__})",
                          "le port répond mais ce n'est pas le webserver d'Arena : vérifie le port"))

    # 4. WebSocket : la composition doit arriver
    t = time.perf_counter()
    try:
        # close_timeout court : Arena peut tarder à répondre à la fermeture (10 s par défaut côté websockets)
        async with websockets.connect(f"ws://{host}:{port}/api/v1", max_size=None, open_timeout=min(2.0, left()),
                                      close_timeout=0.3) as ws:
            msg = json.loads(await asyncio.wait_for(ws.recv(), min(2.0, left())))
            ms = (time.perf_counter() - t) * 1000
        if "layers" in msg:
            name = (msg.get("name") or {}).get("value", "?")
            steps.append(Step("WebSocket", True, f"composition « {name} » : {len(msg['layers'])} couches, "
                                                 f"{len(msg.get('columns', []))} colonnes ({ms:.0f} ms)"))
        else:
            steps.append(Step("WebSocket", False, "connecté mais pas de composition reçue", "version d'Arena trop ancienne ?"))
    except Exception as e:
        steps.append(Step("WebSocket", False, f"échec ({type(e).__name__})",
                          "le REST répond mais pas le WebSocket : proxy ou pare-feu qui filtre les WebSockets ?"))
    return steps


def format_report(host: str, port: int, steps: list[Step], elapsed: float) -> str:
    mark = {True: "OK  ", False: "ÉCHEC", None: " -  "}
    lines = [f"Diagnostic Arena  {host}:{port}  ({elapsed:.1f} s)", ""]
    for s in steps:
        lines.append(f"[{mark[s.ok]}] {s.name:<18} {s.detail}")
        if s.hint and s.ok is False:
            lines.append(f"         -> {s.hint}")
    ok = all(s.ok for s in steps)
    lines += ["", "Tout répond : la passerelle peut piloter Arena." if ok else "Arena n'est pas joignable : voir ci-dessus."]
    return "\n".join(lines)


def check(host: str, port: int) -> tuple[bool, str]:
    t = time.perf_counter()
    steps = asyncio.run(run_check(host, port))
    return all(s.ok for s in steps), format_report(host, port, steps, time.perf_counter() - t)
