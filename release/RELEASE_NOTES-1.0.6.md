# EOS 1.0.6 - Windows x64

Patch release. Unsigned.

## Fixed
- A new project without a package.json-like marker file (empty folder, plain scripts, MQL, non-ASCII folder name) had every write refused. The opened folder is now the project. Drives, the user profile, Desktop, Downloads, temp and system folders are still not.
- In a workspace with a project registry, an unregistered folder is its own project instead of taking the whole workspace's identity and map.
- Activation and the write gate use one identity for unregistered projects.
