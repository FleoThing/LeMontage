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
- [ ] Parser YAML → pipeline object
- [ ] DAG builder (résolution des dépendances entre étapes)
- [ ] Executor (parallélisme, gestion des states)
- [ ] Système de cache / checkpoints (skip si output existe déjà)
- [ ] Gestion des erreurs (retry, skip, on_failure)
- [ ] Logs et progress bar

## Blocks natifs v1
- [ ] `stt` — transcription via faster-whisper
- [ ] `tts` — synthèse vocale via kokoro-onnx
- [ ] `detect_clips` — détection de moments forts
- [ ] `cut` — découpe vidéo via FFmpeg
- [ ] `captions` — génération de sous-titres
- [ ] `export` — rendu final (format, résolution, output path)

## Providers
- [ ] Interface `TTSProvider` (base)
- [ ] Interface `STTProvider` (base)
- [ ] Provider Whisper (faster-whisper)
- [ ] Provider Coqui / kokoro-onnx
- [ ] Provider Ollama (LLM local)

## Paradigmes pipeline
- [ ] Channels (flux de données entre étapes)
- [ ] Matrix (multi-plateforme / multi-langue en une passe)
- [ ] Named outputs (`steps.x.output`)
- [ ] States (pending → running → success / failed / skipped)
- [ ] Wildcards

## Distribution
- [ ] Dockerfile (image slim sans modèles)
- [ ] Script d'installation curl (`install.sh`)
- [ ] Téléchargement des modèles au premier run (`~/.reelflow/models/`)
- [ ] Packaging pip / uv (plus tard)

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
