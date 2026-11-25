# push-xtouch-resolume

Passerelle temps réel entre **Resolume Arena** et deux contrôleurs, **Ableton Push 2** et **Behringer X-Touch** (mode MCU) :
déclenchement des clips, colonnes et decks, opacités, effets composition, tempo, avec les retours d'Arena
(LED RVB et écran du Push, faders motorisés et scribble strips colorés de la X-Touch).

Spécification : [SPEC.md](SPEC.md). Résultats des essais matériels : [spikes/COMPTE_RENDU.md](spikes/COMPTE_RENDU.md).

## Installation avec l'installateur (recommandé)

1. Lancer `PushXTouchResolume_Setup_0.1.0.exe` (aucun droit administrateur requis), puis **Installer**.
   Le programme va dans `%LOCALAPPDATA%\Programs\push-xtouch-resolume`, avec un raccourci dans le menu Démarrer
   et, en option, sur le bureau et au démarrage de Windows.
2. Dans Arena : **Preferences → MIDI**, décocher *Ableton Push 2* et la X-Touch ; **Preferences → Webserver**,
   activer *Webserver & REST API* (port 8080).
3. La passerelle tourne en arrière-plan avec une **icône près de l'horloge**. La pastille est verte quand tout est
   connecté, orange s'il manque un contrôleur, rouge si Arena est injoignable. Clic droit pour voir :
   - l'état d'Arena, du Push 2 (MIDI + écran) et de la X-Touch ;
   - **Ouvrir le journal** (`%APPDATA%\push-xtouch-resolume\logs\pxr.log`) ;
   - **Ouvrir les réglages** (`config.yaml` et `mapping.yaml` dans `%APPDATA%\push-xtouch-resolume`), puis
     **Redémarrer** pour les appliquer ;
   - **Quitter** (éteint les LED et remet la palette d'usine du Push).

Mise à jour : relancer un installateur plus récent, qui arrête proprement la passerelle et garde les réglages.
Désinstallation : *Paramètres Windows → Applications*. Les réglages sont gardés, sauf si la case est cochée.

Pour fabriquer l'installateur : `.venv\Scripts\python.exe packaging\build.py`, qui produit `dist\PushXTouchResolume_Setup_<version>.exe`.

## Arena sur une autre machine (mode réseau)

Les contrôleurs restent branchés en USB sur le PC de la passerelle. L'écran du Push 2 est en USB direct et doit
rester local. La passerelle se connecte à Arena par le réseau : commandes **et** retour d'état passent par le
WebSocket du webserver d'Arena (un seul port TCP, pas d'OSC).

**Sur la machine d'Arena :**

1. Arena > Preferences > **Webserver** : activer *Enable Webserver & REST API* et noter le port (8080 par défaut).
2. Une adresse stable : IP fixe ou réservation DHCP sur le réseau local, ou l'adresse Tailscale (`100.x.y.z`).
3. Pare-feu Windows, si la connexion est refusée (PowerShell **administrateur**, réseau en profil Privé) :

```powershell
New-NetFirewallRule -DisplayName "Resolume Webserver" -Direction Inbound -Protocol TCP -LocalPort 8080 -Action Allow -Profile Private
```

(Remplacer 8080 par le port réel. Il n'y a pas de règle OSC à ajouter : la passerelle n'utilise pas l'OSC.)

**Sur le PC de la passerelle :** dans `config.yaml` (installé : `%APPDATA%\push-xtouch-resolume\config.yaml`) :

```yaml
arena:
  host: 192.168.1.50          # IP de la machine d'Arena (locale ou Tailscale 100.x.y.z) ; 127.0.0.1 = ce PC
  port: 8080                  # port du webserver d'Arena (« web_port » accepté aussi)
  reconnect: {initial_delay_ms: 500, max_delay_ms: 10000}
```

Puis **Redémarrer** depuis l'icône. En console : `python -m pxr --host 192.168.1.50`.

**Diagnostic :** `python -m pxr --check`, ou **Diagnostic Arena** dans le menu de l'icône. En moins de 5 s, il teste
le nom d'hôte, le port du webserver, l'API REST et le WebSocket, et indique la piste à suivre si une étape échoue
(pare-feu, webserver non activé, mauvais port…).

**En direct :** si Arena redémarre ou si le lien coupe, l'écran du Push et les scribble strips de la X-Touch
affichent **ARENA OFFLINE** (en rouge), les commandes sont ignorées et les faders restent en place. La passerelle
retente seule, avec un délai qui double de 0,5 s à 10 s. Au retour, elle recharge l'état complet d'Arena et se
réabonne à tout, sans rejouer les vieux mouvements. Les faders et encodeurs envoient au plus 60 valeurs par seconde
et par paramètre (la dernière valeur arrive toujours) ; clips et boutons partent immédiatement.

## Installation pour le développement (Windows 10/11)

### 1. Python 3.12

`python-rtmidi` n'a pas de version précompilée pour Python 3.14/3.15 : il faut **Python 3.12**
(installeur officiel `python-3.12.10-amd64.exe` sur python.org ; il s'installe à côté des autres versions).

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

### 2. Pilotes

- **Push 2** : aucun pilote à installer. Windows met tout seul le pilote MIDI standard (port MIDI) et **WinUSB**
  (écran). Pas de Zadig, pas de libusb-win32 : la DLL libusb est fournie par le paquet `libusb-package`.
- **X-Touch** : pilote MIDI standard de Windows. La X-Touch doit être en mode **MC** (Mackie Control),
  choisi au démarrage de la X-Touch (voir le manuel Behringer).
- **X-Touch en Ethernet (RTP-MIDI)** : installer rtpMIDI (Tobias Erichsen), créer la session, puis mettre le nom
  du port réseau dans `config.yaml` (`mcu[0].midi`). *Pas encore testé (spike 3b).*

### 3. Réglages d'Arena

- **Preferences → Webserver** : *Enable Webserver & REST API*, port 8080.
- **Preferences → MIDI** : **décocher *Ableton Push 2*** (entrée et sortie). Sinon Arena garde le port et la
  passerelle ne peut pas ouvrir le Push. Laisser aussi la X-Touch décochée.

## Lancement

```powershell
.\.venv\Scripts\python.exe -m pxr
```

Options : `--config config.yaml`, `--mapping mapping.yaml`, `--log-level DEBUG`,
`--dry-run` (sans Arena : composition de démo simulée, pour travailler les retours visuels).
Ctrl+C éteint proprement les contrôleurs.

Tout se reconnecte tout seul sans redémarrer : Arena relancée, Push ou X-Touch débranché puis rebranché.

## Mapping par défaut

Tout est redéfinissable dans [mapping.yaml](mapping.yaml), sans toucher au code.

**Push 2**

| Contrôle | Action |
|---|---|
| Pads, rangées du bas | Clips : rangée 1 = couche 1 ; atténué = chargé, plein + pulsation au BPM = en lecture |
| Rangée au-dessus des couches | Colonnes (allumée = active) |
| ◄ ► | Banque de colonnes (8 par 8) |
| ▲ ▼ | Défilement des couches (au-delà de 7) |
| Boutons de scène | Decks (en haut = deck 1) |
| Mix / Device | Page Couches / page FX composition |
| Encodeurs 1-8 | Couches : opacité des couches ; FX : paramètres de l'effet choisi |
| Encodeur master | Master composition |
| Boutons du haut | Couches : sélection de la couche ; FX : choix de l'effet (Page ◄ ► : effets 9-16) |
| Boutons du bas | Bypass de la couche / de l'effet |
| Play / Tap Tempo | Lecture-pause du tempo / tap |
| Touch strip | Crossfader (si `touchstrip_crossfader: true`) |

**X-Touch**

| Contrôle | Action |
|---|---|
| Faders 1-8 | Opacité des couches (motorisés, ne bougent pas sous le doigt) |
| Fader master | Master composition |
| Encodeurs | Durée de transition de la couche (couronnes LED) |
| Select / Solo / Mute | Sélection / solo / bypass de la couche |
| Écrans | Nom de la couche + clip en lecture, couleur de la couche |
| Bank ◄ ► / Channel ◄ ► | Décalage de 8 / 1 couche |
| Play / Stop / Cycle | Lecture-pause / stop / tap tempo |

## Développement

```powershell
.\.venv\Scripts\python.exe -m pytest
```

```
src/pxr/
  arena.py          client WebSocket Arena (reconnexion, souscriptions, actions)
  fake_arena.py     Arena simulée (--dry-run, tests)
  state.py          miroir local de la composition
  engine.py         mapping : événements -> actions, état -> retours
  midi.py           ports MIDI par motif regex, reconnexion
  drivers/push2.py          Push 2 : pads, boutons, encodeurs, LED, horloge d'animation
  drivers/push2_display.py  écran du Push 2 (USB)
  drivers/mcu.py            Mackie Control (X-Touch, Extender)
spikes/             scripts de découverte de la phase 0 et leur compte rendu
packaging/          compilation (build.py) et installateur (installer.py)
```

## Points ouverts

- **Couleurs Arena** : Arena expose un `colorid` de "1" à "6", pas une couleur RVB. Tant que la correspondance
  n'est pas relevée (`colors.arena_colorid` dans mapping.yaml), chaque couche a une couleur de repli.
- **X-Touch en RTP-MIDI** : pas encore testée.
- Testé et validé sur le matériel le 25/11/2026 : pads, colonnes, decks, encodeurs, écran, page FX (Push) ; faders,
  écrans, Select/Solo/Mute, master, Play (X-Touch). Pas encore confirmés explicitement : flèches ◄ ► ▲ ▼, Play et
  Tap Tempo du Push, Bank/Channel, Stop et Cycle de la X-Touch, touch strip en crossfader.
- Hors v1 : X-Touch Extender (2ᵉ entrée `mcu` dans config.yaml, déjà prévu), interface web, vignettes, OSC.
