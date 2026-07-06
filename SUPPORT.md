# Support

Use the right channel based on the type of request.

## Questions

Open a GitHub Discussion if discussions are enabled. Otherwise, open a regular
issue with the `question` label.

Good questions include:

- What you are trying to build.
- The command you ran.
- Your pipeline YAML, with private paths or data removed.
- Your OS and Python version.

## Bugs

Open a GitHub issue and include:

- The LeMontage version or commit.
- The exact command.
- The pipeline YAML.
- The full error output.
- Whether the issue reproduces with an example pipeline.

For media-specific bugs, include the relevant media properties if possible:

```bash
ffprobe -hide_banner your-video.mp4
```

Do not upload private or copyrighted media unless you have the right to share it.

## Security Issues

Do not open a public issue for security reports. Follow [SECURITY.md](SECURITY.md).

## Feature Requests

Feature requests should explain the workflow, not only the desired implementation.

Useful detail:

- Creator format or use case.
- Example input and desired output.
- Whether it should be a new YAML block, a parameter on an existing block or documentation.
- Any compatibility concern with the current YAML spec.

## Contributing

For setup, checks and pull request expectations, see [CONTRIBUTING.md](CONTRIBUTING.md).
