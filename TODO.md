# Reelflow — TODO

Important : ne jamais te mettre en co-autheur lors des commits, et si tu as des questions qui te donnerait un meilleur contexte, demandez-moi dès le début. Important : exécutez SonarQube localserver après avoir généré du code pour appliquer les meilleurs standards de codage, et créez des tests. Important : créez un . gitignore en fonction de la pile technologique utilisée par l’utilisateur.
met a jour le man a chaque fois si tu ajoutes des choses.

## Scope & Design
- [x] Définir la spec YAML formelle — voir `docs/SPEC.md`
- [x] Définir les commandes CLI — `reelflow run`, `reelflow validate`, `reelflow init`
- [x] Définir les formats d'entrée supportés — MP4 uniquement en v1
- [x] Définir la structure des outputs — `./output/` par défaut, overridable dans le YAML
- [x] Définir le format des outputs XCom — namespaces `steps.<id>.*` (voir SPEC §7)
- [x] Trancher sur le nom définitif — **Reelflow**

## Core Engine
- [x] Parser YAML + validateur contre la spec (`reelflow validate`)
- [x] CLI `run` / `validate` / `init` (run = stub honnête, moteur média à venir)
- [x] Tests unitaires (pytest) + lint (ruff)
- [x] CI GitHub Actions (lint + tests, matrice Python 3.10-3.12)
- [x] DAG builder (résolution des dépendances entre étapes) — `engine/dag.py`
- [x] Executor (parallélisme, gestion des states) — `engine/executor.py`
- [x] Système de cache / checkpoints (skip si output existe déjà)
- [x] Gestion des erreurs (retry, skip, on_failure)
- [x] Logs (reporter ligne par ligne ; barre de progression riche à venir)

## Blocks natifs v1
- [x] `stt` — transcription via faster-whisper
- [x] `tts` — synthèse vocale via kokoro-onnx
- [x] `detect_clips` — détection de moments forts (silence / scene_change)
- [x] `cut` — découpe vidéo via FFmpeg
- [x] `captions` — génération de sous-titres (burn ou sidecar .srt)
- [x] `export` — rendu final (format, résolution, output path)

## Providers
- [x] Interface `TTSProvider` (base)
- [x] Interface `STTProvider` (base)
- [x] Provider Whisper (faster-whisper)
- [x] Provider Coqui / kokoro-onnx
- [ ] Provider Ollama (LLM local) — pas de block LLM en v1 (`engagement` réservé)

## Paradigmes pipeline
- [x] Channels (flux de données entre étapes)
- [x] Matrix (multi-plateforme / multi-langue en une passe)
- [x] Named outputs (`steps.x.output`)
- [x] States (pending → running → success / failed / skipped)
- [ ] Wildcards

## Distribution
- [ ] Dockerfile (image slim sans modèles)
- [ ] Script d'installation curl (`install.sh`)
- [ ] Téléchargement des modèles au premier run (`~/.reelflow/models/`)
- [ ] Packaging pip / uv (plus tard)

## DX / Outillage (v2)
- [ ] `reelflow explain pipeline.yaml` — décrire en clair ce que le pipeline va produire (étapes, fan-out channels, fichiers de sortie) avant de lancer ; sert d'auto-vérification, utile aux pipelines générés par IA
- [ ] JSON Schema publié du format YAML (contrainte + autocomplétion éditeur + cible idéale pour génération IA)
- [ ] Messages de validation qui suggèrent la correction (« valeur inconnue 'silenced', tu voulais 'silence' ? »)

## Hub communautaire (hors v1)
- [ ] Définir la structure du hub (repo GitHub ? API ?)
- [ ] Commande `reelflow publish` / `reelflow pull`
- [ ] Versioning des pipelines partagés
- [ ] Page web du hub

## Communication
- [ ] Démo fonctionnelle (un YAML → une vidéo produite)
- [ ] Post Twitter/X au lancement de la démo
- [ ] Post Reddit (r/Python, r/selfhosted)
- [ ] Show HN (Hacker News) pour la v1 stable
- [ ] Product Hunt pour la v1 stable
