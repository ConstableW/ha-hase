🇩🇪 [Deutsch](../../de/development/contributing.md) | 🇬🇧 English

# Contributing documentation and code

German is HASE's primary and authoritative public documentation language. Build and verify the German page from implementation, executable tests and current references first; then translate that verified page into English. Do not independently derive the two versions. If they disagree, German wins and both should be corrected.

Keep technical identifiers unchanged across languages: Python names, module paths, configuration/YAML keys, method names, status/reason codes and protocol terminology. Every public page must link prominently to its counterpart, and navigation should remain within the selected language.

For code changes, preserve the semantic boundary: `core/` owns meaning; frontends adapt transport and presentation. Add operations only to `core/operations.py`, reuse the shared executor and policies, and test the same contract across frontends.

Historical documents are evidence, not current instructions. Preserve their historical markers and keep the internal index (`../../internal/INDEX.md`, a maintainer document that is not part of the public repository) up to date when adding or superseding a reference.
