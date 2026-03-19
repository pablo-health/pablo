# Contributing to Pablo

Thanks for your interest in improving Pablo! We welcome contributions from the community.

## Getting Started

1. Fork the repo and clone your fork
2. Set up local development (see README.md)
3. Create a branch for your change
4. Make your changes and add tests
5. Run `make check` to verify linting and tests pass
6. Open a pull request

## Guidelines

- **Follow existing patterns** in the codebase
- **Write tests** for new functionality
- **Keep changes focused** — one PR per feature/fix
- **Use clear commit messages** that describe the "why"

## Code Quality

Before submitting a PR:

```bash
make check    # Runs linting + tests
make format   # Auto-fix formatting
```

## What We Accept

- Bug fixes
- Performance improvements
- Documentation improvements
- Accessibility improvements
- New features that benefit solo practitioners

## What Needs Discussion First

Open an issue before working on:

- Large refactors or architectural changes
- New dependencies
- Features that may have HIPAA implications
- Changes to the AI pipeline or verification logic

## Code of Conduct

Be respectful, constructive, and patient. We're building software that helps therapists help their clients — let's keep that spirit in our collaboration.

## Contributor License Agreement

All contributors must sign our [CLA](CLA.md) before their first pull request can be merged. The CLA Assistant bot will prompt you automatically on your first PR. This allows Pablo Health to dual-license the project (AGPL-3.0 for open source, commercial for our hosted offering).

## License

By contributing, you agree that your contributions will be licensed under the AGPL-3.0 license.
