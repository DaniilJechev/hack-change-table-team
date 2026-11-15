# desktop-release Specification

## Purpose
Проверяемая публикация Windows-установщика и контрольной суммы по стабильному тегу версии после CI.

## Requirements

### Requirement: DESKTOP-REL-01 Release version
Windows releases SHALL use stable `vMAJOR.MINOR.PATCH` tags matching
desktop/package.json. A mismatch MUST fail before installer packaging.

#### Scenario: Matching version
- **WHEN** the tag exactly matches the stable desktop package version
- **THEN** the version gate accepts it

#### Scenario: Invalid version
- **WHEN** the tag differs from the package version or uses a non-stable format
- **THEN** the version gate rejects it without publishing a release

### Requirement: DESKTOP-REL-02 Release publication
CI SHALL run all existing checks on release tags and publish the Windows EXE and
SHA256SUMS only after all jobs pass and the commit belongs to main. Publication
SHALL use the workflow token with write permission only in the release job.
Published releases MUST NOT be overwritten; interrupted drafts MAY be resumed.

#### Scenario: Tagged release
- **WHEN** a matching stable tag points to a main commit and all CI jobs pass
- **THEN** its GitHub Release contains the installer and SHA256SUMS

#### Scenario: Branch build
- **WHEN** CI runs for a pull request or main push without a tag
- **THEN** the installer is an artifact and no GitHub Release is published
