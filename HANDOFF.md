# Reelflow — Handoff / reprise du projet

> Document de passation pour reprendre le projet sur une autre machine.
> Généré le 2026-06-16.

---

## 1. C'est quoi Reelflow ?

Un **framework de pipelines déclaratifs en YAML** pour automatiser la création de
vidéos pour les réseaux sociaux (clips, reels, TikToks, shorts, podcasts).

**Objectif affiché :** devenir *le TailwindCSS de la création vidéo automatisée*.

**Vision long terme :** un agent IA génère un fichier YAML à partir d'une simple
intention → le pipeline s'exécute → une vidéo sort à la fin. Un hub
communautaire où les créateurs partagent leurs YAML par trend/niche
(flywheel : plus il y a de YAML, mieux les IA les génèrent).

---

## 2. Décisions déjà prises

| Sujet | Décision |
|---|---|
| Nom | **Reelflow** |
| Langage | **Python** (orchestration) + **FFmpeg** pour les médias (PAS MoviePy) |
| Runtime ML | **ONNX** plutôt que PyTorch (image < 1 Go visée) |
| STT | `faster-whisper` (local) |
| TTS | `kokoro-onnx` (local) |
| LLM | Ollama (local) |
| Providers cloud | **Phase 2 seulement** (ElevenLabs, Deepgram, Claude...) — v1 = 100 % local |
| Input v1 | **MP4 uniquement** |
| Output | `./output/` par défaut, overridable dans le YAML |
| CLI | `run` / `validate` / `init` |
| Publication auto réseaux | **Hors scope v1** |
| Hub communautaire | **Hors v1** |
| Blocks custom / plugins | **Hors v1** |
| Config globale (~/.reelflow/) | **v2** |
| Licence | MIT |
| Distribution | Docker (démo) → script curl → pip/uv |
| Git | branche de travail = **`dev`** ; `main` = remote |

Convention de commits : **Conventional Commits** (`feat:`, `fix:`, `docs:`...).
NE PAS ajouter de ligne `Co-Authored-By`.

---

## 3. État actuel (ce qui marche / ce qui manque)

### ✅ Fait
- Docs : `README.md`, `docs/SPEC.md` (spec YAML v1 formelle), `docs/workflow-example.svg`
- Pipeline d'exemple : `examples/podcast-to-clips.yaml`
- Package Python `src/reelflow/` :
  - `spec.py` — constantes de la spec
  - `validator.py` — validation complète d'un YAML contre la spec v1
  - `cli.py` — commandes `validate` + `init` (fonctionnelles), `run` (stub)
- **31 tests pytest** (validator + CLI), **ruff** clean
- **CI GitHub Actions** (`.github/workflows/ci.yml`) : lint + tests, Python 3.10-3.12

### 🚧 Ce qui NE marche PAS encore
- **Le moteur d'exécution média n'existe pas.** `reelflow run` valide le YAML puis
  s'arrête (exit 2) en disant que le moteur arrive plus tard.
- Donc **aucune vidéo n'est produite** pour l'instant.
- Pas de vrai `video-example.mp4` dans le repo (médias lourds gitignored).

---

## 4. Reprendre sur ta machine (setup)

Prérequis système : **Python ≥ 3.10** et **FFmpeg** (pour la suite, pas pour les tests actuels).

```bash
# FFmpeg (selon ton OS)
sudo apt install ffmpeg      # Ubuntu/Debian
brew install ffmpeg          # macOS
winget install ffmpeg        # Windows

# Récupérer le projet (depuis la clé USB, ou git clone)
cd reelflow

# Installer uv si besoin : https://docs.astral.sh/uv/
# Créer l'environnement + installer le projet en mode dev
uv venv
uv pip install -e ".[dev]"
```

### Commandes utiles

```bash
# Lancer les tests
.venv/bin/pytest -q

# Lint
.venv/bin/ruff check src tests

# Valider le pipeline d'exemple
.venv/bin/reelflow validate examples/podcast-to-clips.yaml

# Générer un pipeline de départ
.venv/bin/reelflow init mon-pipeline.yaml

# "Lancer" (stub pour l'instant — valide puis exit 2)
.venv/bin/reelflow run examples/podcast-to-clips.yaml
```

> Astuce : `source .venv/bin/activate` puis tu peux taper directement `pytest`,
> `ruff`, `reelflow` sans le préfixe `.venv/bin/`.

---

## 5. Prochaine étape (le gros morceau)

Construire le **moteur d'exécution** pour que `run` produise enfin des vidéos.
Plan proposé :

1. **DAG builder** — résoudre l'ordre des étapes + références (`steps.x.output`, channels `emit`/`from`)
2. **Executor** — exécuter les étapes, gérer states / cache / parallélisme
3. **Blocks "mock"** d'abord — pour que `run` marche de bout en bout et reste testable en CI sans FFmpeg
4. **Blocks réels** ensuite, un par un :
   - `stt` (faster-whisper)
   - `detect_clips` (détection de silence via FFmpeg)
   - `cut` (FFmpeg)
   - `captions` (sous-titres incrustés)
   - `export` (rendu final 9:16)

Stratégie validée : commencer par **DAG builder + executor + blocks mock**, puis
brancher les vrais blocks.

---

## 6. Git — où on en est

- Remote : `git@github.com:ffillouxdev/reelflow.git`
- Branche de travail : **`dev`** (déjà poussée)
- `main` : ne contient que le commit initial + LICENSE (PR `dev`→`main` pas encore faite)

```bash
git checkout dev
git pull origin dev    # pour récupérer les derniers commits
```

Historique récent sur `dev` :
```
feat: add pipeline validator, CLI and CI
docs: feature workflow diagram as README hero image
docs: bootstrap Reelflow spec, example pipeline and workflow diagram
Initial commit
```

---

## 7. Fichiers clés à connaître

| Fichier | Rôle |
|---|---|
| `docs/SPEC.md` | Spec YAML v1 — LE document de référence |
| `src/reelflow/validator.py` | Logique de validation |
| `src/reelflow/cli.py` | Point d'entrée CLI |
| `examples/podcast-to-clips.yaml` | Pipeline d'exemple |
| `TODO.md` | Roadmap détaillée (cases cochées = fait) |
| `pyproject.toml` | Deps + config (ruff, pytest, entry point) |
