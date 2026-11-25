# Phase 0 — Comptes rendus des spikes (25/11/2026)

Environnement : Python **3.12.10** dans `.venv` (python-rtmidi n'a pas de wheel pour 3.14/3.15), Arena 7.27.1 rev 15990.

Ports MIDI vus par Windows (mido/rtmidi) :

| Appareil | Entrée | Sortie |
|---|---|---|
| Push 2, port Live | `Ableton Push 2 N` (**occupé**) | `Ableton Push 2 N` (**occupé**) |
| Push 2, port User | `MIDIIN2 (Ableton Push 2) N` | `MIDIOUT2 (Ableton Push 2) N` |
| X-Touch (USB) | `X-Touch N` ✅ | `X-Touch N` ✅ |
| X-Touch, 2ᵉ port | `MIDIIN2 (X-Touch) N` (silencieux) | `MIDIOUT2 (X-Touch) N` |

`N` change d'une machine à l'autre → config par motif regex (`^Ableton Push 2 \d+$`, `^X-Touch \d+$`). Les noms Push correspondent au tableau « Windows » du manuel Ableton.

---

## Spike 1 — Écran Push 2 ✅

Validé visuellement : « l'écran affiche bien » (barres de couleur, texte, barre mobile). Le format des pixels du manuel est donc correct tel quel.

`spikes/s1_push2_display.py`

- L'interface écran (MI_00) utilise **déjà le pilote WinUSB de Microsoft** (`winusb.inf`) : aucune installation de pilote (Zadig, libusb-win32…). Pas de conflit avec le pilote MIDI (MI_01 = `usbaudio`, classe standard).
- `pyusb` + `libusb-package` (DLL libusb embarquée, PyPI) : `claim_interface(0)` OK, envoi bulk sur l'endpoint 0x01 OK.
- Débit mesuré : **25–27 trames/s** en 30 fps demandés, pire envoi d'une trame 27 ms. La limite vient du rendu PIL + encodage numpy en Python, pas de l'USB. C'est suffisant pour l'interface (le manuel : écran noir si rien pendant 2 s).
- `push2-python` **n'est pas sur PyPI** ; l'installation depuis GitHub a été refusée par le garde-fou de sécurité. Le protocole d'affichage du manuel tient en ~30 lignes (`encode()`), donc on peut s'en passer. **Décision à prendre** : garder notre implémentation ou installer push2-python à la main.

## Spike 2 — Arena WebSocket ✅

`spikes/s2_arena_ws.py`. Référence REST complète : servie par Arena sur `http://127.0.0.1:8080/api/docs/rest/swagger.yaml` (non publiée dans le dépôt : c'est un document Resolume).

- `ws://127.0.0.1:8080/api/v1` : 3 messages à la connexion, dans cet ordre : composition complète (objet avec `layers`, sans `type`), `sources_update`, `effects_update`.
- Format client (vérifié dans le JS d'exemple servi par Arena, `/api/docs/example`) :
  - `{"action":"subscribe"|"unsubscribe"|"get","parameter":"/parameter/by-id/<id>"}`
  - `{"action":"set","parameter":"/parameter/by-id/<id>","value":<v>}`
  - `{"action":"trigger","parameter":"/composition/clips/by-id/<id>/connect","value":true}` à l'appui, puis `false` au relâchement (même chose pour `/composition/columns/by-id/<id>/connect` et `/composition/decks/by-id/<id>/select`)
  - `{"action":"post"|"remove","path":...,"body":...}`
- Réponses : `{"type":"parameter_subscribed"|"parameter_update"|"parameter_set"|"parameter_get","id":…,"value":…,"path":…}` ; il existe aussi `thumbnail_update` (`value.id` = clip).
- 121 souscriptions (clips connected/name/colorid, colonnes, decks, opacités, master, tempo, bypass FX) → seulement des `parameter_subscribed`, **aucun trafic au repos**.
- Composition de démo « Example » : 3 couches × 9 colonnes, 4 decks (Footage Shop, Audio Visual, Generators, Wire), 9 effets composition. Clips 1–5 remplis, 6–9 vides.
- États d'un clip (`connected`, ParamState) : `Empty`, `Disconnected`, `Previewing`, `Connected`, `Connected & previewing`. Colonne : `Empty`, `Disconnected`, `Connected`.
- Tempo : `tempocontroller.tempo` (ParamRange 20–500), `play_state` (Play/Pause/Stop), `tempo_tap` / `resync` (ParamEvent).
- ⚠️ **Couleurs** : `colorid` est un ParamChoice dont les options sont `"1"`…`"6"`. Arena n'expose pas de RGB. **À VÉRIFIER** : la correspondance entre ces numéros et les couleurs d'Arena. Test à faire : colorer un clip dans Arena et regarder ce qui change dans le journal.
- **Reste à faire** : lancer `s2_arena_ws.py --seconds 120` pendant qu'on déclenche des clips, des colonnes et des decks à la main, pour voir l'ordre et la fréquence des `parameter_update`.

## Spike 3a — X-Touch USB (MCU) ✅

`spikes/s3_xtouch_mcu.py`, `spikes/midi_listen.py`. Constantes reprises de `LV1-workspace/xtouch-lv1`, déjà validées le 14/10/2026.

- Sorties confirmées visuellement : scribble strips (texte + 8 couleurs, SysEx `F0 00 00 66 14 72 …`), 9 faders motorisés (pitch bend canaux 0–8), couronnes LED (CC 0x30–0x37), LED Select.
- Entrées sur le port `X-Touch N` (le port `MIDIIN2 (X-Touch)` ne reçoit rien) : faders = pitch bend canaux 0–7 et **8 pour le master**, toucher = notes **104–112** (0x68–0x70), vélocité 127 au toucher et 0 au relâchement.
- Pas de latence perceptible en USB (ressenti, pas mesuré).
- **Reste à faire** : (b) le même test en RTP-MIDI Ethernet, et l'enregistrement complet des boutons et encodeurs (la sortie console a été tronquée ; `midi_listen.py` écrit maintenant tout dans `spikes/out/midi_listen.log`).

## Spike 5 — Couleurs Arena (`colorid`) ✅

`spikes/s5_arena_colors.py` : clips 1 à 6 de la couche 1 mis sur colorid "1" à "6", couleurs relevées sur une capture d'Arena.

- colorid 1 = gris (= pas de couleur), 2 = brun rosé, 3 = vert menthe, 4 = vert d'eau foncé, 5 = bleu ardoise, 6 = mauve.
- Le **vert menthe** de l'étiquette « Layer 1 », de « Column 3 » et du deck actif est la couleur de **sélection** d'Arena, pas un colorid. Les couches de la démo ont toutes le colorid 1.
- ⚠️ Donner un colorid à un clip **vide** le transforme en clip non vide (« Clip 1 », état `Disconnected`).
- Les couleurs d'Arena sont ternes et se distinguent surtout par leur clarté → sur les LED, chaque colorid reçoit une teinte franche et distincte (`colors.colorid_led` dans mapping.yaml).

## Spike 6 — Atténuation des pads du Push ✅

`spikes/s6_push2_dim.py` : 3 couleurs × 8 niveaux écrits dans la palette (SysEx 0x03 + 0x05 Reapply).

- Les entrées sombres de la palette **par défaut** (ex. 123 = 64,64,64) restent très lumineuses : l'« atténué » ne se voyait pas (photo du Push).
- Avec une palette écrite par nous, les 8 niveaux 255/128/64/40/24/14/8/4 sont tous distincts et visibles. Le niveau **40/255 (≈ 16 %)** a été choisi pour « clip chargé ».
- → Le driver Push alloue dynamiquement des entrées de palette pour chaque couleur RVB affichée. Il ne touche pas aux entrées 0, 16, 48 et 122-127, et remet la palette d'usine à l'arrêt.

## Spike 4 — Push 2 LED ✅

`spikes/s4_push2_leds.py`. Résultats : `spikes/out/push2_palette.json`, `spikes/out/s4_report.json`.

- **Prérequis** : dans Arena → Preferences → MIDI, *Ableton Push 2* doit être **décoché**. Sinon Arena garde le port Live et la passerelle ne peut pas l'ouvrir. À écrire dans le README.
- On reste en mode **Live**, sur le port `Ableton Push 2 N`. Pas besoin de basculer en mode User, puisque Live n'est pas utilisé.
- Device inquiry : `7E 01 06 02 00 21 1D 67 32 02 00 01 00 47 00 41 7C 46 08 00 01` → Push 2, firmware **1.0.71**.
- Brillance LED lue = **127**. Le Push est donc sur alimentation externe ; sur USB seul, le manuel la limite à 8.
- Palette par défaut lue en entier (128 entrées, 126 couleurs RGB distinctes). Les entrées 122 à 127 correspondent au manuel (blanc, gris clair, gris foncé, bleu, vert, rouge). → On pourra faire la correspondance couleur → index palette sur ce fichier, ou réécrire la palette (cmd 0x03).
- **Disposition des pads confirmée** visuellement (animations rangée par rangée vues dans le bon ordre) : `note = 36 + 8 × rangée + colonne`, rangée 0 = en bas, colonne 0 = à gauche.
- **Animations confirmées** : canal 0 fixe, 4 flash unique, 8/9/10 pulsation (8e, noire, blanche), 13/14/15 clignotement. Synchronisées sur l'horloge MIDI envoyée par la passerelle (FA puis F8 à 24 ppq). → Pour pulser au BPM d'Arena, il suffira d'envoyer l'horloge au tempo d'Arena.
- Entrées observées :
  - Pads : note-on/off canal 0, vélocités assez **basses** (1–37 en appuyant normalement). La sensibilité est réglable (cmd 0x28) si besoin.
  - Encodeurs 1–8 : CC 71–78 relatifs (1, 2 = horaire ; 127, 126, 125 = anti-horaire). Swing = CC 15.
  - **Toucher des encodeurs : notes 0, 1, 2… pour les encodeurs 1, 2, 3** (et note 9 vue en touchant swing/tempo), **pas** la note 71. Touch strip = note 12.
  - Touch strip : pitch bend, valeurs mido −8192…+8191 (0 = centre).
  - Boutons de scène : CC 36–43 ✅. Boutons New/Duplicate/Automate/Fixed length : CC 87–90 ✅.
- **Pas encore vus** (pas appuyés pendant l'écoute) : flèches (CC 44–47 selon le manuel), boutons au-dessus et en dessous de l'écran (102–109 / 20–27), Shift, Play, Tap Tempo, encodeur master. Ils viennent du manuel et resteront marqués « non vérifiés » dans le code tant qu'on ne les a pas vus passer.
