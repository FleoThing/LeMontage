# Instructions projet — Reelflow

## Workflow Git (IMPORTANT)

- **Commite chaque changement cohérent en local, au fur et à mesure** — une
  feature, un fix, un lot de docs = un commit. Ne pas accumuler tout le travail
  dans un seul gros commit en fin de session.
- Messages en style *conventional commits* (`feat:`, `fix:`, `test:`, `docs:`,
  `ci:`, `chore:`), cohérents avec l'historique existant.
- **Ne JAMAIS ajouter de ligne `Co-Authored-By`** (ni aucune mention de
  co-auteur) dans les commits ou les descriptions de PR.
- Ne **push** (`git push`) que si l'utilisateur le demande explicitement.
- Travailler sur `dev` (ou une branche dédiée), jamais directement sur `main`.

## Qualité

- Avant de commiter du code : `ruff check src tests` et `pytest -q` doivent
  passer. Créer des tests pour toute nouvelle logique.
- Tenir à jour la doc quand on ajoute des choses : `docs/SPEC.md` (le manuel du
  format) et `docs/reelflow.1` (la page de manuel CLI).

## Contexte

- v1 : entrée MP4 locale uniquement, exécution 100 % locale (FFmpeg via
  `imageio-ffmpeg`, `faster-whisper`). Pas de provider cloud. La TTS (kokoro-onnx)
  est reportée en v2.
- Demander à l'utilisateur dès le début si un point de contexte est ambigu.
