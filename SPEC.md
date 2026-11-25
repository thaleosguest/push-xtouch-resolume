# push-xtouch-resolume — Spécification v0.1

Passerelle temps réel entre **Resolume Arena** et deux contrôleurs : **Ableton Push 2** et **Behringer X-Touch**.
Objectif : contrôle complet de la composition avec des retours visuels riches (LED RGB, écran Push 2, faders motorisés, scribble strips colorés).

> Règle pour Claude Code : chaque valeur marquée **[À VÉRIFIER]** doit être confirmée dans la source indiquée avant d'être codée en dur. Ne rien inventer : si une info manque, écrire un test de découverte (dump MIDI / dump API) et me montrer le résultat.

---

## 1. Contexte matériel et logiciel

| Élément | Détail |
|---|---|
| Machine | Portable Windows — Arena et la passerelle tournent sur la même machine |
| Arena | 7.27.1, Webserver & REST API activé, port 8080, écoute 0.0.0.0 |
| Push 2 | USB. Deux interfaces : MIDI (pads, boutons, encodeurs, LED) + USB bulk (écran) |
| X-Touch | Mode **MCU** (Mackie Control). Liaison USB **ou** Ethernet RTP-MIDI |
| X-Touch Extender | **Hors périmètre v1**, mais l'architecture doit permettre de l'ajouter (2ᵉ instance du driver MCU) |

## 2. Références obligatoires

- Push 2 : *Ableton Push 2 MIDI and Display Interface Manual* — https://github.com/Ableton/push-interface
- Bibliothèque Python Push 2 : https://github.com/alxdb/push2-python (écran géré ; palettes, clignotement LED, touch strip avancé **non** implémentés → à faire nous-mêmes d'après le manuel Ableton)
- Resolume REST API : https://resolume.com/support/en/restapi
- Resolume WebSocket API : https://resolume.com/support/en/websocket-api
- X-Touch couleurs en mode MCU : SysEx `F0 00 00 66 14 72 c1 c2 c3 c4 c5 c6 c7 c8 F7` (une couleur par strip, les 8 en un seul message). Source communautaire, ajouté par un firmware récent. **[À VÉRIFIER]** table des codes couleur (0–7) et ID d'appareil (`14` = X-Touch, Extender probablement `15`).
- Code existant à réutiliser : dépôt `xtouch-lv1` (module MCU déjà fonctionnel avec couleurs) → copier/adapter le module, ne pas créer de dépendance croisée pour l'instant.

## 3. Stack

- **Python 3.12+** (choisi car push2-python gère l'écran)
- `mido` + `python-rtmidi` — MIDI
- `push2-python` — écran Push 2 (+ code maison pour LED/palette)
- `websockets` / `httpx` — API Arena
- `pydantic` + YAML — config et mapping
- `pytest` — tests
- RTP-MIDI sous Windows : passer par un pilote RTP-MIDI système (ex. rtpMIDI de Tobias Erichsen) qui expose un port MIDI virtuel → la passerelle ne voit qu'un port MIDI. **[À VÉRIFIER]** au spike 0.3.

## 4. Architecture

```
            Arena (WebSocket = état en push, REST = actions)
                          │
                 ┌────────┴────────┐
                 │  arena_client   │  connexion, reconnexion auto, souscriptions
                 └────────┬────────┘
                          │ événements d'état normalisés
                 ┌────────┴────────┐
                 │   state_model   │  miroir local : decks, couches, colonnes,
                 │                 │  clips (nom, couleur, état), effets, BPM
                 └────────┬────────┘
                          │
                 ┌────────┴────────┐
                 │  mapping_engine │  lit mapping.yaml, pages/banques,
                 │                 │  traduit état → feedback et entrées → actions
                 └───┬─────────┬───┘
                     │         │
          ┌──────────┴──┐   ┌──┴───────────┐
          │ push2_driver│   │ mcu_driver   │  (instanciable N fois : X-Touch, Extender…)
          │ MIDI + écran│   │ USB ou RTP   │
          └─────────────┘   └──────────────┘
```

Principes :
- **Arena est la source de vérité.** Les contrôleurs n'affichent que ce qu'Arena confirme (pas d'état optimiste, sauf faders touchés : ne pas renvoyer de position au fader tant qu'il est touché).
- **Drivers isolés** : aucun driver ne connaît Arena. Ils exposent des événements (`pad_pressed(x,y,vel)`, `encoder_turned(i,delta)`, `fader_moved(i,val)`) et des méthodes de rendu (`set_pad_color`, `set_fader`, `set_strip(text, color)`, `draw_display(frame)`).
- **Reconnexion automatique** de tout (Arena, Push, X-Touch) sans redémarrer l'appli — usage live.
- **Throttling** des sorties : fusionner les mises à jour LED/faders par trame (~30–60 Hz) pour ne pas saturer le MIDI.

## 5. Phase 0 — Spikes de validation (à faire AVANT le reste)

Chaque spike = un script autonome dans `spikes/` + un compte rendu.

1. **Écran Push 2 sous Windows** : afficher une image de test via push2-python. Vérifier si un pilote libusb/WinUSB est nécessaire et s'il entre en conflit avec le pilote Ableton. **Risque principal du projet.**
2. **Arena WebSocket** : se connecter, s'abonner, logger les événements quand on déclenche un clip à la main. Lister les champs disponibles (nom, couleur, état connected, thumbnail).
3. **X-Touch** : (a) USB en MCU, faders + scribble strips + couleurs ; (b) même chose via RTP-MIDI Ethernet. Mesurer la latence ressentie.
4. **Push 2 LED** : afficher la palette par défaut sur les 64 pads, tester l'animation (clignotement/pulsation) décrite dans le manuel. **[À VÉRIFIER]** numéros de notes des pads et nom du port « User » sous Windows.

## 6. Mapping par défaut (v1)

Composition de démo actuelle : 3 couches × 9 colonnes, 4 decks, 9 effets composition.

### Push 2
| Contrôle | Action Arena | Retour |
|---|---|---|
| Pads, rangées 1–N (bas → haut) | Clip couche N, colonne 1–8 de la banque courante | Couleur du clip (ou de la couche) atténuée = chargé ; pleine + pulsation au BPM = en lecture ; éteint = vide |
| Rangée de pads au-dessus des couches | Déclenchement de colonne | Colonne active allumée |
| Flèches gauche/droite | Banque de colonnes (1–8 / 9–16) | — |
| Boutons de scène (côté droit) | Changement de deck | Deck actif allumé |
| 8 encodeurs | Page « Couches » : opacité couches + master ; page « FX » : paramètres d'effets composition | Valeurs sur l'écran |
| Écran | Noms des clips / couches, valeurs des encodeurs, page active, BPM | — |
| Touch strip | Crossfader (si activé dans la config uniquement) | — |

### X-Touch (MCU)
| Contrôle | Action Arena | Retour |
|---|---|---|
| Faders 1–8 | Opacité couches (banque de 8) | Faders motorisés suivent Arena |
| Fader master | Master composition | Motorisé |
| Encodeurs 1–8 | Paramètre sélectionné par couche (ex. vitesse clip) | Anneaux LED |
| Select / Solo / Mute | Sélection / solo / bypass couche | LED |
| Scribble strips | Nom de couche + nom du clip en lecture | Couleur = couleur de la couche |
| Transport | Play/pause BPM, tap tempo | LED |

Tout doit être redéfinissable dans `mapping.yaml` sans toucher au code.

## 7. Fichiers de config

- `config.yaml` : adresse Arena, ports MIDI (par nom, avec motif regex), mode USB/RTP du X-Touch, fréquence de rafraîchissement.
- `mapping.yaml` : pages, banques, assignations.

## 8. Phase 2 (après v1)

- Interface tactile web servie par la passerelle (grille de clips + faders), pour l'écran tactile du portable ou une tablette, alimentée par le même `state_model`.
- Support X-Touch Extender (2ᵉ instance `mcu_driver`).
- Vignettes de clips sur l'écran Push 2.
- Entrée/sortie OSC optionnelle (TouchOSC).

## 9. Non-objectifs v1

- Pas de modification de la composition (création de couches, chargement de médias) depuis les contrôleurs.
- Pas de sauvegarde de composition depuis la passerelle.

## 10. Qualité

- Tests unitaires du `mapping_engine` et du `state_model` avec un faux client Arena et de faux drivers.
- Logs structurés, niveau réglable.
- Un mode `--dry-run` sans Arena (état simulé) pour développer les retours visuels.
- README avec installation pas à pas sous Windows (pilotes compris).
